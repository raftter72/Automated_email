
import imaplib
import smtplib
import email
import os
import sys
import time
import html
import re
import socket

from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr

# Sprawdzaj wiadomosci z ostatnich 24 godzin
OKNO_MIN = 1440

GMAIL_USER = os.environ["GMAIL_USER"]

KONTA = [
    {
        "nazwa": "Yahoo",
        "imap": "imap.mail.yahoo.com",
        "smtp": "smtp.mail.yahoo.com",
        "user": os.environ["YAHOO_USER"],
        "haslo": os.environ["YAHOO_PASS"],
        "wyswietlana_nazwa": "Poczta Yahoo",
    },
    {
        "nazwa": "WP",
        "imap": "imap.wp.pl",
        "smtp": "smtp.wp.pl",
        "user": os.environ["WP_USER"],
        "haslo": os.environ["WP_PASS"],
        "wyswietlana_nazwa": "Poczta WP",
    },
]


def przygotuj_wiadomosc(konto, raw):
    orig = email.message_from_bytes(
        raw, policy=policy.default
    )

    od = str(orig.get("From", "?"))
    data = str(orig.get("Date", "?"))
    temat = str(orig.get("Subject", "(bez tematu)"))

    msg = EmailMessage()

    # Nadawca to konto Yahoo lub WP
    msg["From"] = formataddr((
        konto["wyswietlana_nazwa"],
        konto["user"]
    ))

    msg["To"] = GMAIL_USER
    msg["Subject"] = temat

    # POPRAWKA SMTP 550:
    # Reply-To musi pasowac do konta nadawczego.
    msg["Reply-To"] = konto["user"]

    # Oryginalny nadawca pozostaje w tresci
    naglowek = (
        f"Wiadomosc przekazana ze skrzynki: {konto['nazwa']}\n"
        f"Oryginalny nadawca: {od}\n"
        f"Data: {data}\n\n"
    )

    body = orig.get_body(
        preferencelist=("plain", "html")
    )

    if body is None:
        msg.set_content(
            naglowek + "(brak tresci)"
        )

    elif body.get_content_type() == "text/html":
        msg.set_content(
            naglowek + "(wiadomosc HTML)"
        )

        info = (
            f"<p><b>Skrzynka:</b> "
            f"{html.escape(konto['nazwa'])}<br>"
            f"<b>Oryginalny nadawca:</b> "
            f"{html.escape(od)}<br>"
            f"<b>Data:</b> "
            f"{html.escape(data)}</p><hr>"
        )

        msg.add_alternative(
            info + body.get_content(),
            subtype="html"
        )

    else:
        msg.set_content(
            naglowek + body.get_content()
        )

    # Zachowaj zalaczniki
    for zal in orig.iter_attachments():
        dane = zal.get_payload(decode=True)

        if dane is None:
            continue

        typ = zal.get_content_type()

        if "/" not in typ:
            typ = "application/octet-stream"

        maintype, subtype = typ.split("/", 1)

        msg.add_attachment(
            dane,
            maintype=maintype,
            subtype=subtype,
            filename=zal.get_filename() or "zalacznik"
        )

    return msg


def bezpieczny_komunikat(s):
    if isinstance(s, bytes):
        s = s.decode(
            "utf-8", errors="replace"
        )

    s = str(s)

    # Ukryj adresy e-mail
    s = re.sub(
        r"[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}",
        "[EMAIL]",
        s
    )

    # Ukryj dane poufne
    for nazwa in (
        "YAHOO_PASS",
        "WP_PASS",
        "GMAIL_PASS",
        "YAHOO_USER",
        "WP_USER",
        "GMAIL_USER"
    ):
        sekret = os.environ.get(nazwa, "")
        if sekret:
            s = s.replace(
                sekret, "[UKRYTE]"
            )

    return s[:350]


def pokaz_blad(nazwa, etap, e):
    print(
        f"{nazwa}: BLAD na etapie [{etap}]",
        flush=True
    )

    print(
        f"{nazwa}: typ bledu: {type(e).__name__}",
        flush=True
    )

    if isinstance(
        e, smtplib.SMTPResponseException
    ):
        print(
            f"{nazwa}: kod SMTP: {e.smtp_code}",
            flush=True
        )

        print(
            f"{nazwa}: odpowiedz SMTP: "
            f"{bezpieczny_komunikat(e.smtp_error)}",
            flush=True
        )

    elif isinstance(e, imaplib.IMAP4.error):
        print(
            f"{nazwa}: blad IMAP",
            flush=True
        )

    elif isinstance(
        e, (socket.timeout, TimeoutError, OSError)
    ):
        print(
            f"{nazwa}: blad polaczenia sieciowego",
            flush=True
        )

    else:
        print(
            f"{nazwa}: blad przetwarzania",
            flush=True
        )


def obsluz_konto(konto):
    nazwa = konto["nazwa"]

    imap = None
    smtp = None
    etap = "start"

    try:
        print(
            f"\n--- Konto: {nazwa} ---",
            flush=True
        )

        etap = "IMAP polaczenie"

        imap = imaplib.IMAP4_SSL(
            konto["imap"],
            993,
            timeout=30
        )

        etap = "IMAP logowanie"

        imap.login(
            konto["user"],
            konto["haslo"]
        )

        print(
            f"{nazwa}: IMAP OK",
            flush=True
        )

        etap = "IMAP wybor INBOX"

        status, _ = imap.select(
            "INBOX",
            readonly=False
        )

        if status != "OK":
            raise RuntimeError(
                "INBOX niedostepny"
            )

        od_daty = (
            datetime.now(timezone.utc)
            - timedelta(days=2)
        ).strftime("%d-%b-%Y")

        etap = "IMAP wyszukiwanie"

        status, dane = imap.search(
            None,
            "UNSEEN",
            "SINCE",
            od_daty
        )

        if status != "OK":
            raise RuntimeError(
                "Wyszukiwanie IMAP nie powiodlo sie"
            )

        numery = (
            dane[0].split()
            if dane and dane[0]
            else []
        )

        print(
            f"{nazwa}: nieprzeczytane: {len(numery)}",
            flush=True
        )

        wyslane = 0
        za_stare = 0

        for num in numery:
            etap = "IMAP pobieranie"

            status, msg_data = imap.fetch(
                num,
                "(INTERNALDATE BODY.PEEK[])"
            )

            if status != "OK":
                raise RuntimeError(
                    "Blad pobierania wiadomosci"
                )

            czesci = [
                x for x in msg_data
                if isinstance(x, tuple)
                and len(x) >= 2
            ]

            if not czesci:
                raise RuntimeError(
                    "Brak danych wiadomosci"
                )

            czas_info = (
                imaplib.Internaldate2tuple(
                    czesci[0][0]
                )
            )

            if czas_info is None:
                raise RuntimeError(
                    "Nieprawidlowa data IMAP"
                )

            czas = time.mktime(
                czas_info
            )

            if (
                time.time() - czas
                > OKNO_MIN * 60
            ):
                za_stare += 1
                continue

            etap = "Przygotowanie wiadomosci"

            msg = przygotuj_wiadomosc(
                konto,
                czesci[0][1]
            )

            if smtp is None:
                etap = "SMTP polaczenie"

                print(
                    f"{nazwa}: laczenie SMTP...",
                    flush=True
                )

                smtp = smtplib.SMTP_SSL(
                    konto["smtp"],
                    465,
                    timeout=30
                )

                print(
                    f"{nazwa}: polaczenie SMTP OK",
                    flush=True
                )

                etap = "SMTP logowanie"

                smtp.login(
                    konto["user"],
                    konto["haslo"]
                )

                print(
                    f"{nazwa}: logowanie SMTP OK",
                    flush=True
                )

            etap = "SMTP wysylanie"

            smtp.send_message(
                msg,
                from_addr=konto["user"],
                to_addrs=[GMAIL_USER]
            )

            print(
                f"{nazwa}: SMTP wyslano wiadomosc",
                flush=True
            )

            etap = "IMAP oznaczanie jako przeczytane"

            status, _ = imap.store(
                num,
                "+FLAGS",
                r"(\Seen)"
            )

            if status != "OK":
                raise RuntimeError(
                    "Wyslano, ale nie oznaczono "
                    "jako przeczytane"
                )

            wyslane += 1

        print(
            f"{nazwa}: za stare: {za_stare}",
            flush=True
        )

        print(
            f"{nazwa}: przeslano: {wyslane}",
            flush=True
        )

        return True

    except Exception as e:
        pokaz_blad(
            nazwa,
            etap,
            e
        )

        return False

    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass

        if imap is not None:
            try:
                imap.logout()
            except Exception:
                pass


def main():
    blad = False

    for konto in KONTA:
        if not obsluz_konto(konto):
            blad = True

    if blad:
        sys.exit(1)


if __name__ == "__main__":
    main()
