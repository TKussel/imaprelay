import email
import imaplib
import smtplib
import logging
import time

from .import util
from .connection import make_imap_connection, make_smtp_connection

log = logging.getLogger(__name__)

BATCH_SIZE = 10

class RelayError(Exception):
    pass

class IMAPError(RelayError):
    pass

class Relay(object):
    def __init__(self, to, inbox, archive):
        self.to = to
        self.inbox = inbox
        self.archive = archive

    def relay(self):
        self.imap = make_imap_connection()
        self.smtp = make_smtp_connection()

        data = self._chk(self.imap.list())
        folders = [util.parse_folder_line(line)[2] for line in data]

        if self.inbox not in folders:
            raise RelayError('No "{0}" folder found! Where should I relay messages from?'.format(self.inbox))

        if self.archive not in folders:
            raise RelayError('No "{0}" folder found! Where should I archive messages to?'.format(self.archive))

        data = self._chk(self.imap.select(self.inbox, readonly=True))

        log.info('Relaying {num} messages from {inbox}'.format(num=data[0], inbox=self.inbox))

        # Take BATCH_SIZE messages and relay them
        def get_next_slice():
            data = self._chk(self.imap.search(None, 'ALL'))
            msg_ids = [x for x in data[0].split(' ') if x != '']
            msg_slice, msg_ids = msg_ids[:BATCH_SIZE], msg_ids[BATCH_SIZE:]
            return msg_slice

        msg_slice = get_next_slice()
        while msg_slice:
            self._relay_messages(msg_slice)
            msg_slice = get_next_slice()

        # Close connections
        self.imap.close()
        self.imap.logout()
        self.smtp.quit()

    def _relay_messages(self, message_ids):
        log.debug("Relaying messages {0}".format(message_ids))

        # Get messages and relay them
        message_ids = ','.join(message_ids)
        msg_data = self._chk(self.imap.fetch(message_ids, '(RFC822)'))

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                eml = email.message_from_string(response_part[1])
                res = self.smtp.sendmail(eml['from'], self.to, eml.as_string())

                log.debug("Sent message '{subj}' from {from_} to {to}".format(from_=eml['from'],
                                                                              to=self.to,
                                                                              subj=eml['subject']))

        # Copy messages to archive folder
        self._chk(self.imap.copy(message_ids, self.archive))

        # Mark messages as deleted on server
        self._chk(self.imap.store(message_ids, '+FLAGS', r'(\Deleted)'))

        # Expunge
        self._chk(self.imap.expunge())

    def loop(self, interval=30):
        try:
            while 1:
                self.relay()
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            log.info('Closing connections')

            try:
                self.imap.close()
            except imaplib.IMAP4.error:
                pass

            try:
                self.imap.logout()
            except (imaplib.IMAP4.error, AttributeError):
                pass

            try:
                self.smtp.quit()
            except smtplib.SMTPServerDisconnected:
                pass

    def _chk(self, res):
        typ, data = res
        if typ != 'OK':
            raise IMAPError("typ '{0}' was not 'OK!".format(typ))
        return data
