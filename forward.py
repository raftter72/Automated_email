
import email
import html
import imaplib
import os
import re
import smtplib
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr

OKNO_MIN = 1440

GMAIL_USER = os.environ['GMAIL_USER']
KONTA = [
    {'nazwa': 'Yahoo', 'imap': 'imap.mail.yahoo.com', 'smtp': 'smtp.mail.yahoo.com',
     'user': os.environ['YAHOO_USER'], 'haslo': os.environ['YAHOO_PASS'],
     'wyswietlana_nazwa': 'Poczta Yahoo'},
    {'nazwa': 'WP', 'imap': 'imap.wp.pl', 'smtp': 'smtp.wp.pl',
     'user': os.environ['WP_USER'], 'haslo': os.environ['WP_PASS'],
     'wyswietlana_nazwa': 'Poczta WP'},
]


def przygotuj_wiadomosc(konto, raw):
    orig = email.message_from_bytes(raw, policy=policy.default)
    od = str(orig.get('From', '?'))
    data = str(orig.get('Date', '?'))
    temat = str(orig.get('Subject', '(bez tematu)'))

    msg = EmailMessage()
    msg['From'] = formataddr((konto['wyswietlana_nazwa'], konto['user']))
    msg['To'] = GMAIL_USER
    msg['Subject'] = temat

    # Bez Reply-To: serwer Yahoo odrzucal ten naglowek (SMTP 550).
    naglowek = (
        f"Wiadomosc przekazana ze skrzynki: {konto['nazwa']}\n"
        f"Oryginalny nadawca: {od}\nData: {data}\n\n"
    )

    body = orig.get_body(preferencelist=('plain', 'html'))

    if body is None:
        msg.set_content(naglowek + '(brak tresci)')
    elif body.get_content_type() == 'text/html':
        msg.set_content(naglowek + '(wiadomosc HTML)')
        info = (
            f"<p><b>Skrzynka:</b> {html.escape(konto['nazwa'])}<br>"
            f"<b>Oryginalny nadawca:</b> {html.escape(od)}<br>"
            f"<b>Data:</b> {html.escape(data)}</p><hr>"
        )
        msg.add_alternative(info + body.get_content(), subtype='html')
    else:
        msg.set_content(naglowek + body.get_content())

    for zal in orig.iter_attachments():
        dane = zal.get_payload(decode=True)
        if dane is None:
            continue

        typ = zal.get_content_type()
        if '/' not in typ:
            typ = 'application/octet-stream'

        maintype, subtype = typ.split('/', 1)

        msg.add_attachment(
            dane,
            maintype=maintype,
            subtype=subtype,
            filename=zal.get_filename() or 'zalacznik'
        )

    return msg


def bezpieczny_komunikat(komunikat):
    if isinstance(komunikat, bytes):
        komunikat = komunikat.decode('utf-8', errors='replace')

    s = str(komunikat)

    for klucz in (
        'YAHOO_PASS', 'WP_PASS', 'GMAIL_PASS',
        'YAHOO_USER', 'WP_USER', 'GMAIL_USER'
    ):
        sekret = os.environ.get(klucz, '')
        if sekret:
            s = s.replace(sekret, '[UKRYTE]')

    s = re.sub(r'[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', s)
    return s[:300]


def opis_bledu(nazwa, etap, exc):
    print(f'{nazwa}: BLAD [{etap}]: {type(exc).__name__}', flush=True)

    if isinstance(exc, smtplib.SMTPResponseException):
        print(f'{nazwa}: kod SMTP: {exc.smtp_code}', flush=True)
        print(
            f'{nazwa}: odpowiedz: {bezpieczny_komunikat(exc.smtp_error)}',
            flush=True
        )

    elif isinstance(exc, imaplib.IMAP4.error):
        print(f'{nazwa}: blad protokolu IMAP', flush=True)

    elif isinstance(exc, (socket.timeout, TimeoutError, OSError)):
        print(f'{nazwa}: blad sieciowy', flush=True)


def odrzucona_jako_spam(exc):
    """
    Rozpoznaje trwale odrzucenie wiadomosci
    przez filtr antyspamowy serwera SMTP.
    """
    if not isinstance(exc, smtplib.SMTPResponseException):
        return False

    if exc.smtp_code < 500 or exc.smtp_code >= 600:
        return False

    odpowiedz = exc.smtp_error

    if isinstance(odpowiedz, bytes):
        odpowiedz = odpowiedz.decode('utf-8', errors='replace')

    odpowiedz = str(odpowiedz).lower()

    return any(
        slowo in odpowiedz
        for slowo in ('spam', 'antyspam')
    )


def zamknij_smtp(smtp):
    if smtp is not None:
        try:
            smtp.quit()
        except Exception:
            try:
                smtp.close()
            except Exception:
                pass


def polacz_smtp(konto):
    smtp = smtplib.SMTP_SSL(konto['smtp'], 465, timeout=30)

    try:
        smtp.login(konto['user'], konto['haslo'])
    except Exception:
        zamknij_smtp(smtp)
        raise

    return smtp


def usun_wiadomosc(imap, uid):
    """
    Oznacza wiadomosc o konkretnym UID jako usunieta.
    Faktyczne usuniecie nastepuje podczas EXPUNGE.
    """
    status, _ = imap.uid(
        'STORE',
        uid,
        '+FLAGS.SILENT',
        r'(\Deleted)'
    )

    if status != 'OK':
        raise RuntimeError(
            'Nie udalo sie oznaczyc wiadomosci do usuniecia'
        )


def obsluz_konto(konto):
    nazwa = konto['nazwa']

    imap = None
    smtp = None

    wyslane = 0
    usuniete_spam = 0
    pominiete = 0
    bledne = 0

    etap = 'IMAP polaczenie'

    try:
        print(f'--- Konto: {nazwa} ---', flush=True)

        imap = imaplib.IMAP4_SSL(
            konto['imap'], 993, timeout=30
        )

        etap = 'IMAP logowanie'
        imap.login(konto['user'], konto['haslo'])

        print(f'{nazwa}: IMAP OK', flush=True)

        etap = 'IMAP INBOX'
        status, _ = imap.select('INBOX', readonly=False)

        if status != 'OK':
            raise RuntimeError('Nie mozna otworzyc INBOX')

        od_daty = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).strftime('%d-%b-%Y')

        etap = 'IMAP wyszukiwanie'

        status, dane = imap.uid(
            'SEARCH', None, 'UNSEEN', 'SINCE', od_daty
        )

        if status != 'OK':
            raise RuntimeError('Blad wyszukiwania')

        uids = dane[0].split() if dane and dane[0] else []

        print(
            f'{nazwa}: nieprzeczytane: {len(uids)}',
            flush=True
        )

        for uid in uids:
            etap_wiadomosci = 'IMAP pobieranie'
            wyslano_ten_mail = False

            try:
                status, msg_data = imap.uid(
                    'FETCH',
                    uid,
                    '(INTERNALDATE BODY.PEEK[])'
                )

                if status != 'OK':
                    raise RuntimeError('Blad pobierania')

                czesci = [
                    x for x in msg_data
                    if isinstance(x, tuple) and len(x) >= 2
                ]

                if not czesci:
                    raise RuntimeError('Brak danych wiadomosci')

                czas_info = imaplib.Internaldate2tuple(
                    czesci[0][0]
                )

                if czas_info is None:
                    raise RuntimeError('Nieprawidlowa data IMAP')

                if time.time() - time.mktime(czas_info) > OKNO_MIN * 60:
                    pominiete += 1
                    continue

                etap_wiadomosci = 'Przygotowanie wiadomosci'

                msg = przygotuj_wiadomosc(
                    konto, czesci[0][1]
                )

                if smtp is None:
                    etap_wiadomosci = 'SMTP polaczenie i logowanie'

                    smtp = polacz_smtp(konto)

                    print(
                        f'{nazwa}: SMTP logowanie OK',
                        flush=True
                    )

                etap_wiadomosci = 'SMTP wysylanie'

                smtp.send_message(
                    msg,
                    from_addr=konto['user'],
                    to_addrs=[GMAIL_USER]
                )

                wyslano_ten_mail = True
                wyslane += 1

                print(
                    f'{nazwa}: SMTP wyslano wiadomosc',
                    flush=True
                )

                # USUWANIE WIADOMOSCI PO UDANEJ WYSYLCE
                etap_wiadomosci = 'IMAP usuwanie wyslanej wiadomosci'

                usun_wiadomosc(imap, uid)

                print(
                    f'{nazwa}: wyslana wiadomosc oznaczona do usuniecia',
                    flush=True
                )

            except Exception as e:

                # USUWANIE WIADOMOSCI ODRZUCONEJ JAKO SPAM
                if (
                    etap_wiadomosci == 'SMTP wysylanie'
                    and odrzucona_jako_spam(e)
                ):
                    opis_bledu(nazwa, etap_wiadomosci, e)

                    try:
                        usun_wiadomosc(imap, uid)
                        usuniete_spam += 1

                        print(
                            f'{nazwa}: trwale odrzucona jako spam '
                            f'- oznaczona do usuniecia',
                            flush=True
                        )

                    except Exception as blad_usuwania:
                        bledne += 1
                        opis_bledu(
                            nazwa,
                            'IMAP usuwanie spamu',
                            blad_usuwania
                        )

                else:
                    bledne += 1
                    opis_bledu(nazwa, etap_wiadomosci, e)

                    if wyslano_ten_mail:
                        print(
                            f'{nazwa}: UWAGA - wyslano, ale nie usunieto; '
                            f'mozliwy duplikat!',
                            flush=True
                        )
                    else:
                        print(
                            f'{nazwa}: nie wyslano; '
                            f'wiadomosc pozostaje nieprzeczytana',
                            flush=True
                        )

                # ODNOWIENIE POLACZENIA PO BLEDZIE SMTP
                if etap_wiadomosci.startswith('SMTP'):
                    zamknij_smtp(smtp)
                    smtp = None

        # FAKTYCZNE USUNIECIE WIADOMOSCI
        etap = 'IMAP EXPUNGE'

        status, _ = imap.expunge()

        if status != 'OK':
            raise RuntimeError(
                'Nie udalo sie wykonac EXPUNGE'
            )

        print(f'{nazwa}: za stare: {pominiete}', flush=True)
        print(f'{nazwa}: przeslano i oznaczono do usuniecia: {wyslane}', flush=True)
        print(f'{nazwa}: spam oznaczony do usuniecia: {usuniete_spam}', flush=True)
        print(f'{nazwa}: bledne: {bledne}', flush=True)

        return bledne == 0

    except Exception as e:
        opis_bledu(nazwa, etap, e)
        return False

    finally:
        zamknij_smtp(smtp)

        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass


def main():
    wyniki = [
        obsluz_konto(konto)
        for konto in KONTA
    ]

    if not all(wyniki):
        sys.exit(1)


if __name__ == '__main__':
    main()
