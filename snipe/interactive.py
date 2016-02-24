# -*- encoding: utf-8 -*-
# Copyright © 2014 the Snipe contributors
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

import inspect
import os
import contextlib


def _keyword(name):
    def __get_keyword(*args, **kw):
        return kw.get(name, None)
    return __get_keyword

context = _keyword('context')
window = _keyword('window')
keystroke = _keyword('keystroke')
keyseq = _keyword('keyseq')
keymap = _keyword('keymap')
argument = _keyword('argument')


def integer_argument(*args, **kw):
    arg = kw.get('argument', None)
    if isinstance(arg, int) or arg is None:
        return arg
    if arg == '-':
        return -1
    return 4**len(arg)


def positive_integer_argument(*args, **kw):
    arg = integer_argument(*args, **kw)
    if not isinstance(arg, int): #coercion happens in integer_argument
        return arg
    return abs(arg)


def isinteractive(*args, **kw):
    return True


def call(callable, *args, **kw):
    d = {}
    parameters = inspect.signature(callable).parameters
    for (name, arg) in parameters.items():
        if arg.annotation != inspect.Parameter.empty:
            val = arg.annotation(*args, **kw)
            if val is None and arg.default != inspect.Parameter.empty:
                val = arg.default
            d[name] = val
        elif arg.default == inspect.Parameter.empty:
            raise Exception(
                'insufficient defaults calling %s' % (repr(callable),))
    return callable(**d)


def complete_filename(left, right):
    path, prefix = os.path.split(left)
    completions = [
        name + '/' if os.path.isdir(os.path.join(path, name)) else name
        for name in os.listdir(path or '.')
        if name.startswith(prefix)]

    prefix = os.path.commonprefix(completions)
    for name in completions:
        yield os.path.join(path, prefix), name[len(prefix):]


class UnCompleter:
    def __init__(self):
        self.candidates = []
        self.live = False

    def matches(self, sofar=''):
        return []

    def roll(self, p):
        pass

    def roll_to(self, s):
        pass

    @staticmethod
    def check(x, y):
        return False

    def expand(self, value, index):
        return None, None


class Completer:
    def __init__(self, iterable):
        self.candidates = list(iterable)
        self.live = bool(self.candidates)

    def matches(self, value=''):
        return [
            (n, c)
            for n, c in enumerate(self.candidates)
            if not value or self.check(value, c)]

    def roll(self, p):
        self.candidates = self.candidates[p:] + self.candidates[:p]

    def roll_to(self, s):
        with contextlib.suppress(ValueError):
            i = self.candidates.index(s)
            self.roll(i)

    @staticmethod
    def check(x, y):
        return x in y

    def expand(self, value):
        # should expand e.g. 'a' out of [ 'aaa', 'aaab', 'caaa'] to 'aaa'
        # but...
        m = self.matches(value)
        if m:
            result = m[0][1]
            return result
        return value
