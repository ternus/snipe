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

import inspect


def context(*args, **kw):
    return kw.get('context', None)


def keystroke(*args, **kw):
    return kw.get('keystroke', None)


def argument(*args, **kw):
    return kw.get('argument', None)


def integer_argument(*args, **kw):
    arg = kw.get('argument', None)
    if isinstance(arg, int):
        return arg
    if arg == '-':
        return 0
    return 4**len(arg)


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
