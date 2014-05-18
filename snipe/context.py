# -*- encoding: utf-8 -*-
# Copyright © 2014 Karl Ramm
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following
# disclaimer in the documentation and/or other materials provided
# with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND
# CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS
# BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED
# TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF
# THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.

import unicodedata
import contextlib
import re
import logging
import time
import asyncio

from . import messages
from . import ttyfe
from . import roost


def bind(*seqs):
    def decorate(f):
        f.snipe_seqs = seqs
        return f
    return decorate


class Window(object):
    def __init__(self, frontend, prototype=None, destroy=lambda: None):
        self.fe = frontend
        self.keymap = {}
        self.renderer = None
        self.keymap = Keymap()
        #XXX should really be walking the inheritance tree so the stuff
        #lower in the tree wins
        for f in (getattr(self, name) for name in dir(self)):
            if hasattr(f, 'snipe_seqs'):
                for seq in f.snipe_seqs:
                    self.keymap[seq] = f
        self.active_keymap = self.keymap
        self.log = logging.getLogger(
            '%s.%x' % (self.__class__.__name__, id(self),))
        if prototype is None:
            self.cursor = None
            self.frame = None
        else:
            self.cursor = prototype.cursor
            self.frame = prototype.frame
        self.destroy = destroy

    def input_char(self, k):
        try:
            self.log.debug('got key %s', repr(k))
            try:
                v = self.active_keymap[k]
            except KeyError:
                v = None
            if not callable(v):
                self.active_keymap = v
            else:
                self.active_keymap = self.keymap
                ret = v(k)
                if asyncio.iscoroutine(ret):
                    def catch_and_log(coro):
                        try:
                            yield from coro
                        except:
                            self.log.exception('Executing complex command')
                            self.whine(k)

                    t = asyncio.Task(catch_and_log(ret))

        except Exception as e:
            self.log.exception('executing command from keymap')
            self.whine(k)
            self.active_keymap = self.keymap

    def read_string(self, prompt, content=None, height=1):
        f = asyncio.Future()

        def done_callback(result):
            f.set_result(result)
            self.fe.popdown_window()#XXX might not always be the right one

        def destroy_callback():
            if not f.done():
                f.set_exception(Exception('Operation Aborted'))

        from .editor import ShortPrompt, LongPrompt
        if height > 2:
            window = LongPrompt
        else:
            window = ShortPrompt
        self.fe.popup_window(
            window(
                self.fe,
                prompt=prompt,
                content=content,
                callback=done_callback,
                destroy=destroy_callback,
                ),
            height=height,
            )
        self.fe.redisplay()

        yield from f

        return f.result()

    @bind('Control-X Control-C')
    def quit(self, k):
        asyncio.get_event_loop().stop()

    def whine(self, k):
        self.fe.notify()

    @bind('Control-Z')
    def stop(self, k):
        self.fe.sigtstp(None, None)

    def view(self, origin=None, direction=None):
        yield 0, [(('visible',), '')]

    @bind('Control-X 2')
    def split_window(self, k):
        self.fe.split_window(self.__class__(self.fe, prototype=self))

    @bind('Control-X 0')
    def delete_window(self, k):
        self.fe.delete_current_window()

    @bind('Control-X 1')
    def popdown(self, k):
        self.fe.popdown_window()

    @bind('Control-X o')
    def other_window(self, k):
        self.fe.switch_window(1)

    @bind('Control-X e')#XXX
    def split_to_editor(self, k):
        from .editor import Editor
        self.fe.split_window(Editor(self.fe))

    @bind('Control-X t')#XXX
    def test_ui(self, k):
        streeng = yield from self.read_string('floop> ', content='zoge')
        self.log.debug(
            'AAAA %s',
            ''.join(reversed(streeng)),
            )

class Messager(Window):
    def __init__(self, *args, **kw):
        super(Messager, self).__init__(*args, **kw)
        #SPACE
        #^n ^p ↓ ↑ j k NEXT PREV
        self.cursor = next(self.fe.context.backends.walk(time.time(), False))
        self.frame = self.cursor

    def view(self, origin, direction='forward'):
        for x in self.fe.context.backends.walk(origin, direction == 'forward'):
            s = str(x)
            if s and s[-1] != '\n':
                s += '\n'
            if x is self.cursor:
                lines = s.splitlines()
                yield x, [
                    (('visible', 'standout'), lines[0] + '\n'),
                    ((), '\n'.join(lines[1:]) + '\n'),
                    ]
            else:
                yield x, [((), s)]

    @bind('n')
    def next_message(self, k):
        it = iter(self.fe.context.backends.walk(self.cursor))
        try:
            next(it)
            self.cursor = next(it)
        except StopIteration:
            self.whine('No more messages')

    @bind('p')
    def prev_message(self, k):
        it = iter(self.fe.context.backends.walk(self.cursor, False))
        try:
            next(it)
            self.cursor = next(it)
        except StopIteration:
            self.whine('No more messages')

    @bind('s')
    def send(self, k, recipient=''):
        message = yield from self.read_string(
            '[roost] send --> ',
            height=10,
            content=recipient + '\n' if recipient else '',
            )
        params, body = message.split('\n', 1)
        yield from self.fe.context.roost.send(params, body)

    @bind('f')
    def followup(self, k):
        yield from self.send(k, self.cursor.followupstr())

    @bind('r')
    def reply(self, k):
        yield from self.send(k, self.cursor.replystr())

class Context(object):
    # per-session state and abstact control
    def __init__(self, ui):
        self.ui = ui
        self.ui.context = self
        #XXX kludge so the kludged sending can find the roost backend
        self.roost = roost.Roost(
            conf={'context': self}) # XXX figure out a better
                                    # way to communicate this
        self.backends = messages.AggregatorBackend(
            backends = [
                messages.StartupBackend(),
#                messages.SyntheticBackend(conf={'count': 100}),
                self.roost,
                ],)
        self.ui.initial(Messager(self.ui))



@contextlib.contextmanager
def ignores(*exceptions):
    try:
        yield
    except exceptions:
        pass


class Keymap(dict):
    def __init__(self, d={}):
        super(Keymap, self).__init__()
        self.update(d)

    def update(self, d):
        for k, v in d.items():
            if hasattr(v, 'items'):
                self[k] = Keymap(v)
            else:
                self[k] = v

    def __repr__(self):
        return (
            self.__class__.__name__
            + '('
            + super(Keymap, self).__repr__()
            + ')'
            )

    def __getitem__(self, key):
        if not hasattr(key, 'lower'):
            return super(Keymap, self).__getitem__(key)
        else:
            key, rest = self.split(key)
            v = super(Keymap, self).__getitem__(key)
            if key is None:
                return None # default?
            if rest:
                return v[rest]
            return v

    def __setitem__(self, key, value):
        if not hasattr(key, 'lower'):
            return super(Keymap, self).__setitem__(key, value)
        else:
            key, rest = self.split(key)
            if key is None:
                return
            if rest is None:
                super(Keymap, self).__setitem__(key, value)
            else:
                try:
                    v = super(Keymap, self).__getitem__(key)
                except KeyError:
                    v = None
                if v is None:
                    v = Keymap()
                    super(Keymap, self).__setitem__(key, v)
                if not hasattr(v, '__getitem__'):
                    raise KeyError(repr(key) + 'is not a keymap')
                v[rest] = value

    def __delitem__(self, key):
        if not hasattr(key, 'lower'):
            return super(Keymap, self).__delitem__(key)
        else:
            key, rest = self.split(key)
            if rest is None:
                super(Keymap, self).__delitem__(key)
            else:
                v = super(Keymap, self).__getitem__(key)
                if not hasattr(v, '__getitem__'):
                    raise KeyError(repr(key) + 'is not a keymap')
                del v[rest]

    modifier_aliases = {
        'ctl': 'control',
        'alt': 'meta',
        }
    modifiers = ['control', 'shift', 'meta', 'hyper', 'super']
    all_modifiers = modifiers + list(modifier_aliases.keys())

    keyseq_re = re.compile(
        '^(?P<modifiers>((' + '|'.join(all_modifiers) + ')-)*)'
        + r'((?P<char>.)|\[(?P<name>[^]]+)\])'
        + r'(|(\s+(?P<rest>\S.*)))'
        + '$',
        re.IGNORECASE
        )

    other_keys_spec = [
        (('escape','esc'), '\x1b'),
        (('delete', 'del'), '\x7f'),
        (('line feed', 'linefeed', 'newline'), '\x0a'),
        (('carriage return', 'return'), '\x0d'),
        (('tab',), '\x09'),
        ]

    other_keys = {}
    for (names, value) in other_keys_spec:
        for name in names:
            other_keys[name] = value

    unother_keys = {
        value: names[0] for (names, value) in other_keys_spec}

    @staticmethod
    def split(keyseqspec):
        if not hasattr(keyseqspec, 'lower'):
            return keyseqspec, None

        if len(keyseqspec) == 1:
            return keyseqspec, None

        match = Keymap.keyseq_re.match(keyseqspec)

        if not match:
            raise TypeError(
                'Invalid Key Sequence Specification', repr(keyseqspec))

        d = match.groupdict()

        modifiers = d.get('modifiers', '-')[:-1].split('-')
        modifiers = set(
            Keymap.modifier_aliases.get(modifier, modifier).lower()
            for modifier in modifiers if modifier)

        key = d['char']
        rest = d['rest']

        name = d['name']
        if key is None:
            with ignores(KeyError):
                key = unicodedata.lookup(name.upper())

        if key is None:
            with ignores(KeyError):
                key = Keymap.other_keys.get(name.lower())

        if key is None:
            with ignores(KeyError):
                key = ttyfe.key.get(name.upper())

        if key is None:
            raise TypeError('unknown name: %s' % name)

        if 'hyper' in modifiers or 'super' in modifiers:
            return None, None #valid but untypable

        if 'control' in modifiers:
            if not hasattr(key, 'upper'):
                # XXX ignoring control+function keys for now
                return None, None #valid but untypable
            if key == '?':
                key = '\x7f'
            elif ord('@') <= ord(key.upper()) <= ord('_'):
                key = chr(ord(key.upper()) - ord('@'))
            else:
                return None, None #valid but untypable
            modifiers.remove('control')

        if 'shift' in modifiers:
            # XXX ignoring SLEFT et al for now
            if not hasattr(key, 'upper'):
                # XXX ignoring control+function keys for now
                return None, None #valid but untypable
            # XXX ignore e.g. shift-1 (!, on a US keyboard, argh) for now
            key = key.upper()
            modifiers.remove('shift')

        if 'meta' in modifiers:
            if key in Keymap.unother_keys:
                name = '[' + Keymap.unother_keys[key].upper() + ']'
            elif ord(key) < ord(' '):
                name = (
                    'Control-['
                    + unicodedata.name(unicode(chr(ord(key) + ord('@'))))
                    + ']'
                    )
            else:
                name = '[' + unicodedata.name('key') + ']'
            if rest:
                name += ' ' + rest
            rest = name
            key = '\x1b' # ESC
            modifiers.remove('meta')

        assert bool(modifiers) == False

        return key, rest


try:
    unicode('foo')
except NameError:
    unicode = lambda x: x # glue for python 3
