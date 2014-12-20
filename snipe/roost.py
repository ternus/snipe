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


import asyncio
import collections
import itertools
import time
import shlex
import os
import urllib.parse
import contextlib
import re
import pwd

from . import messages
from . import _rooster
from . import util
from . import filters


class Roost(messages.SnipeBackend):
    name = 'roost'

    backfill_count = util.Configurable(
        'roost.backfill_count', 8,
        'Keep backfilling until you have this many messages'
        ' unless you hit the time limit',
        coerce=int)
    backfill_length = util.Configurable(
        'roost.backfill_length', 24 * 3600 * 7,
        'only backfill this looking for roost.backfill_count messages',
        coerce=int)
    url = util.Configurable(
        'roost.url', 'https://roost-api.mit.edu')
    service_name = util.Configurable(
        'roost.servicename', 'HTTP',
        "Kerberos servicename, you probably don't need to change this")
    realm = util.Configurable(
        'roost.realm', 'ATHENA.MIT.EDU',
        'Zephyr realm that roost is fronting for')
    signature = util.Configurable(
        'roost.signature', pwd.getpwuid(os.getuid()).pw_gecos.split(',')[0],
        'Name-ish field on messages')

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.messages = []
        self.r = _rooster.Rooster(self.url, self.service_name)
        self.chunksize = 128
        self.loaded = False
        self.backfilling = False
        self.new_task = asyncio.Task(self.r.newmessages(self.new_message))

    def shutdown(self):
        self.new_task.cancel()
        # this is kludgy, but make sure the task runs a tick to
        # process its cancellation
        try:
            asyncio.get_event_loop().run_until_complete(self.new_task)
        except asyncio.CancelledError:
            pass
        super().shutdown()

    @property
    def principal(self):
        return self.r.principal

    @asyncio.coroutine
    def send(self, paramstr, body):
        import getopt

        self.log.debug('send paramstr=%s', paramstr)

        flags, recipients = getopt.getopt(shlex.split(paramstr), 'c:i:O:')

        flags = dict(flags)
        self.log.debug('send flags=%s', repr(flags))

        if not recipients:
            recipients=['']

        for recipient in recipients:
            message = {
                'class': flags.get('-c', 'MESSAGE'),
                'instance': flags.get('-i', 'PERSONAL'),
                'recipient': recipient,
                'opcode': flags.get('-O', ''),
                'signature': flags.get('-s', self.signature),
                'message': body,
                }

            self.log.debug('sending %s', repr(message))

            result = yield from self.r.send(message)
            self.log.info('sent to %s: %s', recipient, repr(result))

    @asyncio.coroutine
    def new_message(self, m):
        msg = RoostMessage(self, m)
        self.messages.append(msg)
        self.redisplay(msg, msg)

    def backfill(self, filter, target=None, count=0, origin=None):
        if not self.loaded:
            self.log.debug('triggering backfill')
            msgid = None
            if self.messages:
                msgid = self.messages[0].data['id']
                if origin is None:
                    origin = self.messages[0].time
            asyncio.Task(self.do_backfill(msgid, filter, target, count, origin))

    @util.coro_cleanup
    def do_backfill(self, start, mfilter, target, count, origin):
        yield from asyncio.sleep(.0001)

        @contextlib.contextmanager
        def backfillguard():
            if self.backfilling:
                yield True
            else:
                self.log.debug('entering guard')
                self.backfilling = True
                yield False
                self.backfilling = False
                self.log.debug('leaving guard')

        with backfillguard() as already:
            if already:
                self.log.debug('already backfiling')
                return

            if mfilter is None:
                mfilter = lambda m: True

            if self.loaded:
                self.log.debug('no more messages to backfill')
                return
            self.log.debug('backfilling')
            chunk = yield from self.r.messages(start, self.chunksize)

            if chunk['isDone']:
                self.log.info('IT IS DONE.')
                self.loaded = True
            ms = [RoostMessage(self, m) for m in chunk['messages']]
            count += len([m for m in ms if mfilter(m)])
            ms.reverse()
            self.messages = ms + self.messages
            self.log.warning('%d messages, total %d', count, len(self.messages))

            # how far back in time to go
            if target is not None:
                when = target
            else:
                when = origin - self.backfill_length

            if (count < self.backfill_count and ms and ms[0].time > when):
                self.backfill(mfilter, count=count, origin=origin)

            self.redisplay(ms[0], ms[-1])
            self.log.debug('done backfilling')


class RoostMessage(messages.SnipeMessage):
    def __init__(self, backend, m):
        super().__init__(backend, m['message'], m['receiveTime'] / 1000)
        self.data = m
        self._sender = RoostPrincipal(backend, m['sender'])

        self.personal = self.data['recipient'] \
          and self.data['recipient'][0] != '@'
        self.outgoing = self.data['sender'] == self.backend.r.principal

    def __str__(self):
        return (
            'Class: {class_} Instance: {instance} Recipient: {recipient}'
            '{opcode}\n'
            'From: {signature} <{sender}> at {date}\n'
            '{body}\n').format(
            class_=self.data['class'],
            instance=self.data['instance'],
            recipient=self.data['recipient'],
            opcode=(
                ''
                if not self.data['opcode']
                else ' [{}]'.format(self.data['opcode'])),
            signature=self.data['signature'],
            sender=self.sender,
            date=time.ctime(self.data['time'] / 1000),
            body=self.body + ('' if self.body and self.body[-1] == '\n' else '\n'),
            )

    def display(self, decoration):
        tags = self.decotags(decoration)
        instance = self.data['instance']
        instance = instance or "''"
        chunk = []

        if self.personal:
            if self.outgoing:
                chunk += [(tags + ('bold',), '-> ')]
                chunk += [(tags + ('bold',), self.field('recipient'))]
                chunk.append((tags, ' '))
            else:
                chunk += [(tags + ('bold',), '(personal) ')]

        if not self.personal or self.data['class'].lower() != 'message':
            chunk += [
                (tags, '-c '),
                (tags + ('bold',), self.data['class']),
                ]
        if instance.lower() != 'personal':
            chunk += [
                (tags, ' -i '),
                (tags + ('bold',), instance),
                ]

        if self.data['recipient'] and self.data['recipient'][0] == '@':
            chunk += [(tags + ('bold',), ' ' + self.data['recipient'])]

        if self.data['opcode']:
            chunk += [(tags, ' [' + self.data['opcode'] + ']')]

        chunk += [
            (tags, ' <' ),
            (tags + ('bold',), self.field('sender')),
            (tags, '>'),
            ]

        sig = self.data.get('signature', '').strip()
        if sig:
            sigl = sig.split('\n')
            sig = '\n'.join(sigl[:1] + ['    ' + s for s in sigl[1:]])
            self.backend.log.debug('signature: %s', repr(sig))
            chunk += [
                (tags, ' '),
                (tags + ('bold',), sig),
                ]

        chunk.append(
            (tags + ('right',),
             time.strftime(
                ' %H:%M:%S', time.localtime(self.data['time'] / 1000))))

        body = self.body
        if body:
            if not body.endswith('\n'):
                body += '\n'
            chunk += [(tags, body)]

        return chunk

    class_un = re.compile(r'^(un)*')
    class_dotd = re.compile(r'(\.d)*$')
    def canon(self, field, value):
        if field == 'sender':
            value = str(value)
            atrealmlen = len(self.backend.realm) + 1
            if value[-atrealmlen:] == '@' + self.backend.realm:
                return value[:-atrealmlen]
        elif field == 'class':
            value = value.lower() #XXX do proper unicode thing
            x1, x2 = self.class_un.search(value).span()
            value = value[x2:]
            x1, x2 = self.class_dotd.search(value).span()
            value = value[:x1]
        elif field == 'instance':
            value = value.lower() #XXX do proper unicode thing
            x1, x2 = self.class_dotd.search(value).span()
            value = value[:x1]
        elif field == 'opcode':
            value = value.lower().strip()
        return value

    def reply(self):
        l = []
        if self.data['recipient'] and self.data['recipient'][0] != '@':
            if self.data['class'].upper() != 'MESSAGE':
                l += ['-c', self.data['class']]
            if self.data['instance'].upper() != 'PERSONAL':
                l += ['-i', self.data['instance']]
        if self.outgoing:
            l.append(self.data['recipient'])
        else:
            l.append(self.sender.short())

        return self.backend.name + '; ' + ' '.join(shlex.quote(s) for s in l)

    def followup(self):
        l = []
        if self.data['recipient'] and self.data['recipient'][0] != '@':
            return self.reply()
        if self.data['class'].upper() != 'MESSAGE':
            l += ['-c', self.data['class']]
        if self.data['instance'].upper() != 'PERSONAL':
            l += ['-i', self.data['instance']]
        if self.data['recipient']:
            l += [self.data['recipient']] # presumably a there should be a -r?
        return self.backend.name + '; ' + ' '.join(shlex.quote(s) for s in l)

    def filter(self, specificity=0):
        if self.personal:
            if str(self.sender) == self.backend.principal:
                conversant = self.field('recipient')
            else:
                conversant = self.field('sender')
            return filters.And(
                filters.Truth('personal'),
                filters.Or(
                    filters.Compare('=', 'sender', conversant),
                    filters.Compare('=', 'recipient', conversant)))
        elif self.field('class'):
            nfilter = filters.Compare('=', 'class', self.field('class'))
            if specificity > 0:
                nfilter = filters.And(
                    nfilter,
                    filters.Compare('=', 'instance', self.field('instance')))
            if specificity > 1:
                nfilter = filters.And(
                    nfilter,
                    filters.Compare('=', 'sender', self.field('sender')))
            return nfilter

        return super().filter(specificity)


class RoostPrincipal(messages.SnipeAddress):
    def __init__(self, backend, principal):
        self.principal = principal
        super().__init__(backend, [principal])

    def __str__(self):
        return self.principal

    def short(self):
        atrealmlen = len(self.backend.realm) + 1
        if self.principal[-atrealmlen:] == '@' + self.backend.realm:
            return self.principal[:-atrealmlen]
        return self.principal

    def reply(self):
        return self.backend.name + '; ' + self.short()


class RoostTriplet(messages.SnipeAddress):
    def __init__(self, backend, class_, instance, recipient):
        self.class_ = class_
        self.instance = instance
        self.recipient = recipient
        super().__init__(backend, [class_, instance, recipient])
