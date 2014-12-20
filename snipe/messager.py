#!/usr/bin/python3
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


import time
import datetime
import traceback
import pprint

from . import filters
from . import roost
from . import keymap
from . import window
from . import help
from . import editor
from . import util


class Messager(window.Window, window.PagingMixIn):
    default_filter = util.Configurable(
        'default_filter', None,
        'Default filter for messager windows',
        validate=filters.validatefilter,
        )

    def __init__(self, *args, filter_new = None, prototype=None, **kw):
        super().__init__(*args, **kw)

        if prototype is None:
            self.cursor = next(self.fe.context.backends.walk(None, False))
            self.filter_reset()
        else:
            self.filter = prototype.filter
            self.filter_stack = list(prototype.filter_stack)
            self.cursor = prototype.cursor

        if filter_new is not None:
            self.filter_replace(filter_new)

        self.secondary = None
        self.keymap['[space]'] = self.pagedown
        self.keymap.interrogate(help)
        # the following will be interesting in the advent of non-singleton
        # backends; maybe this should just interrogate the modules instead
        for backend in self.context.backends:
            self.keymap.interrogate(backend)
            self.keymap.interrogate(backend.__class__.__module__)
        self.rules = []
        for (filt, decor) in self.context.conf.get('rules', []):
            try:
                self.rules.append((filters.makefilter(filt), decor))
            except:
                self.log.exception(
                    'error in filter %s for decor %s', filt, decor)

    def focus(self):
        if self.secondary is not None:
            self.cursor = self.secondary
            self.secondary = None

    def walk(self, origin, direction, backfill_to=False):
        return self.fe.context.backends.walk(
            origin, direction, self.filter, backfill_to)

    def view(self, origin, direction='forward'):
        it = self.walk(origin, direction != 'forward')
        try:
            next(it)
            prev = next(it)
        except StopIteration:
            prev = None

        for x in self.walk(origin, direction == 'forward'):
            try:
                decoration = {}
                for filt, decor in self.rules:
                    if filt(x):
                        decoration.update(decor)
                chunk = x.display(decoration)
            except:
                chunk = [
                    (('bold',), repr(x) + '\n'),
                    ((), traceback.format_exc()),
                    ((), pprint.pformat(x.data) + '\n'),
                    ]

            def dateof(m):
                if m is None or m.time in (float('inf'), float('-inf')):
                    return None
                return datetime.datetime.fromtimestamp(m.time).date()

            if x.time != float('inf') and (dateof(prev) != dateof(x)):
                    yield x, [(
                    ('bold',),
                    time.strftime('\n%A, %B %d, %Y\n\n', time.localtime(x.time)))]

            if x is self.cursor or x is self.secondary:
                if not chunk:
                    # this is a bug so it will do the wrong thing sometimes
                    yield x, [(('visible', 'standout'), '\n')]
                    continue

                # carve off the first line
                first = []
                while True:
                    if not chunk:
                        # we ran out of chunks without hitting a \n
                        first[-1] = (first[-1][0], first[-1][1] + '\n')
                        break
                    tags, text = chunk[0]
                    if '\n' not in text and 'right' not in tags:
                        first.append((tags, text))
                        chunk = chunk[1:]
                    elif 'right' in tags:
                        first.append((tags, text))
                        chunk = chunk[1:]
                        break
                    else:
                        line, rest = text.split('\n', 1)
                        first.append((tags, line + '\n'))
                        chunk = [(tags, rest)] + chunk[1:]
                        break

                if x is self.cursor:
                    first = (
                        [(first[0][0] + ('visible',), first[0][1])] + first[1:])
                if x is self.secondary or self.secondary is None:
                    first = [
                        (tags + ('standout',), text) for (tags, text) in first]
                yield x, first + chunk
            else:
                yield x, chunk

            prev = x

    def check_redisplay_hint(self, hint):
        if super().check_redisplay_hint(hint):
            return True
        mrange = hint.get('messages')
        if mrange:
            head, sill = self.renderer.display_range()
            m1, m2 = mrange
            self.log.debug('head=%s, sill=%s', repr(head), repr(sill))
            self.log.debug('m1=%s, m2=%s', repr(m1), repr(m2))
            self.log.debug('max(head, m1)=%s', repr(max(head, m1)))
            self.log.debug('min(sill, m2)=%s', repr(min(sill, m2)))
            if max(head, m1) <= min(sill, m2):
                self.log.debug('True!')
                return True
        self.log.debug("Fals.e")
        return False

    @keymap.bind('Control-n', 'n', 'j', '[down]')
    def next_message(self):
        self.move(True)

    @keymap.bind('Control-p', 'p', 'k', '[up]')
    def prev_message(self):
        self.move(False)

    def move(self, forward):
        self.log.debug(
            'move %s: cursor: %s',
            'forward' if forward else 'backward',
            repr(self.cursor),
            )
        target=None
        if not forward:
            target=float('-inf')
        it = iter(self.walk(self.cursor, forward, backfill_to=target))
        try:
            intermediate = next(it)
            self.log.debug(
                'move %s: intermediate: %s',
                'forward' if forward else 'backward',
                repr(intermediate),
                )
            self.cursor = next(it)
            self.log.debug(
                'move %s: cursor: %s',
                'forward' if forward else 'backward',
                repr(self.cursor),
                )
        except StopIteration:
            self.whine('No more messages')

    @keymap.bind('s')
    def send(self, recipient='', msg=None):
        sill = self.renderer.display_range()[1]
        if sill.time == float('inf'): #XXX omega message is visible
            self.secondary = self.cursor
            self.cursor = sill

        kw = {}
        if msg is not None:
            kw['modes'] = [editor.ReplyMode(msg)]

        message = yield from self.read_string(
            'send --> ',
            height=10,
            content=recipient + '\n' if recipient else '',
            history='send',
            fill=True,
            **kw)
        if '\n' not in message:
            message += '\n'
        params, body = message.split('\n', 1)
        yield from self.fe.context.backends.send(params, body)

    def replymsg(self):
        replymsg = self.cursor
        if replymsg.time == float('inf'):
            it = self.walk(self.cursor, False)
            next(it)
            replymsg = next(it)
        return replymsg

    @keymap.bind('f')
    def followup(self):
        msg = self.replymsg()
        yield from self.send(msg.followup(), msg)

    @keymap.bind('r')
    def reply(self):
        msg = self.replymsg()
        yield from self.send(msg.reply(), msg)

    @keymap.bind('[END]', 'Shift-[END]', '[SEND]', 'Meta->', '>')
    def last(self):
        self.cursor = next(self.walk(float('inf'), False))

    @keymap.bind('[HOME]', 'Shift-[HOME]', '[SHOME]', 'Meta-<', '<')
    def first(self):
        self.cursor = next(self.walk(float('-inf'), True))

    def filter_replace(self, new_filter):
        self.filter = new_filter

        if self.filter is not None and not self.filter(self.cursor):
            # if filter is none, self.cursor is valid.
            with util.stopwatch('finding new cursor for filter'):
                self.cursor = next(self.walk(self.cursor, True))
            self.reframe()

    @keymap.bind('Meta-/ 0')
    def filter_reset(self):
        self.filter_stack = []
        self.filter_replace(
            filters.makefilter(self.default_filter)
            if self.default_filter
            else None)

    @keymap.bind('Meta-/ =')
    def filter_edit(self):
        s = '' if self.filter is None else str(self.filter)

        s = yield from self.read_string('Filter expression:\n', s, 5)

        self.filter_replace(filters.makefilter(s))

    @keymap.bind('Meta-/ -')
    def filter_everything(self):
        self.filter_push_and_replace(filters.No())

    def filter_clear_decorate(self, decoration):
        self.rules = [
            (filt, decor) for (filt, decor) in self.rules if filt != self.filter]
        self.rules.append((self.filter, decoration))
        self.context.conf['rules'] = [
            (filts, decor)
            for (filts, decor) in self.context.conf.get('rules', [])
            if filts != str(self.filter)
            ]
        self.context.conf['rules'].append((str(self.filter), decoration))
        self.context.conf_write()
        self.filter_reset()

    @keymap.bind('Meta-/ g')
    def filter_foreground_background(self):
        fg = yield from self.read_string('Foreground: ')
        bg = yield from self.read_string('Background: ')
        self.filter_clear_decorate({'foreground': fg, 'background': bg})

    @keymap.bind('Meta-/ f')
    def filter_foreground(self):
        fg = yield from self.read_string('Foreground: ')
        self.filter_clear_decorate({'foreground': fg})

    @keymap.bind('Meta-/ b')
    def filter_background(self):
        bg = yield from self.read_string('Background: ')
        self.filter_clear_decorate({'background': bg})

    def filter_push_and_replace(self, new_filter):
        if self.filter is not None:
            self.filter_stack.append(self.filter)
        self.filter_replace(new_filter)

    def filter_push(self, new_filter):
        if self.filter is None:
            self.filter_push_and_replace(new_filter)
        else:
            self.filter_push_and_replace(filters.And(self.filter, new_filter))

    @keymap.bind('Meta-/ c')
    def filter_class(self):
        class_ = yield from self.read_string(
            'Class: ', self.cursor.field('class'))
        self.filter_push(filters.Compare('=', 'class', class_))

    @keymap.bind('Meta-/ C')
    def filter_class_exactly(self):
        class_ = yield from self.read_string(
            'Class: ', self.cursor.field('class', False))
        self.filter_push(filters.Compare('==', 'class', class_))

    @keymap.bind('Meta-/ p')
    def filter_personals(self):
        self.filter_push(filters.Truth('personal'))

    @keymap.bind('Meta-/ s')
    def filter_sender(self):
        sender = yield from self.read_string(
            'Sender: ', self.cursor.field('sender'))
        self.filter_push(filters.Compare('=', 'sender', sender))

    @keymap.bind('Meta-/ /')
    def filter_cleverly(self):
        message = self.cursor
        if message.personal:
            if str(message.sender) == message.backend.principal:
                conversant = message.field('recipient')
            else:
                conversant = message.field('sender')
            self.filter_push(
                filters.And(
                    filters.Truth('personal'),
                    filters.Or(
                        filters.Compare('=', 'sender', conversant),
                        filters.Compare('=', 'recipient', conversant))))
        elif message.field('class'):
            self.filter_push(
                filters.Compare('=', 'class', message.field('class')))
        else:
            self.whine("Can't deduce what to filter on")

    @keymap.bind("Meta-/ Meta-/")
    def filter_pop(self):
        if not self.filter_stack:
            self.filter_reset()
        else:
            self.filter = self.filter_stack.pop()

    @keymap.bind('Meta-/ S')
    def save_default_filter(self):
        if self.filter:
            self.default_filter = str(self.filter)
            self.context.conf_write()
            self.filter_reset()

    @keymap.bind('Meta-i')
    def show_message_data(self):
        from pprint import pformat

        self.show(
            repr(self.cursor)
            + '\n'
            + pformat(getattr(self.cursor, 'data', None)))

