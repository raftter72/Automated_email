
import imaplib
import smtplib
import email
import os
import sys
import time
import html
import socket

from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import formataddr

# Sprawdzamy wiadomosci z ostatnich 24 godzin
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
    orig = email.message_from_bytes(raw, policy=policy.default)

    od = str(orig.get("From", "?"))
    data = str(orig.get("Date", "?"))
    temat = str(orig.get("Subject", "(bez tematu)"))

    msg = EmailMessage()

    msg["From"] = formataddr((
        konto["wyswietlana_nazwa"],
        konto["user"]
    ))
    msg["To"] = GMAIL_USER
    msg["Subject"] = temat

    reply = orig.get("Reply-To") or orig.get("From")
    if reply:
        msg["Reply-To"] = str(reply)

    naglowek = (
        f"Wiadomosc przekazana ze skrzynki: {konto['nazwa']}\n"
        f"Oryginalny nadawca: {od}\n"
        f"Data: {data}\n\n"
    )

    body = orig.get_body(preferencelist=("plain", "html"))

    if body is None:
        msg.set_content(naglowek + "(brak tresci)")

    elif body.get_content_type() == "text/html":
        msg.set_content(naglowek + "(wiadomosc HTML)")

        info = (
            f"<p><b>Skrzynka:</b> {html.escape(konto['nazwa'])}<br>"
            f"<b>Od:</b> {html.escape(od)}<br>"
            f"<b>Data:</b> {html.escape(data)}</p><hr>"
        )
        msg.add_alternative(info + body.get_content(), subtype="html")

    else:
        msg.set_content(naglowek + body.get_content())

    for zal in orig.iter_attachments():
        dane = zal.get_payload(decode=True)
        if dane is None:
            continue

        maintype, subtype = zal.get_content_type().split("/", 1)
        msg.add_attachment(
            dane,
            maintype=maintype,
            subtype=subtype,
            filename=zal.get_filename() or "zalacznik"
        )

    return msg


def pokaz_blad(nazwa, etap, e):
    print(
        f"{nazwa}: BLAD na etapie [{etap}]",
        flush=True
    )
    print(
        f"{nazwa}: typ bledu: {type(e).__name__}",
        flush=True
    )

    if isinstance(e, imaplib.IMAP4.error):
        print(
            f"{nazwa}: serwer IMAP odrzucil operacje",
            flush=True
        )
    elif isinstance(e, smtplib.SMTPAuthenticationError):
        print(
            f"{nazwa}: serwer SMTP odrzucil logowanie",
            flush=True
        )
    elif isinstance(e, smtplib.SMTPException):
        print(
            f"{nazwa}: blad protokolu SMTP",
            flush=True
        )
    elif isinstance(e, (socket.timeout, TimeoutError, OSError)):
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
        print(f"\n--- Konto: {nazwa} ---", flush=True)

        etap = "IMAP polaczenie"
        imap = imaplib.IMAP4_SSL(
            konto["imap"], 993, timeout=30
        )

        etap = "IMAP logowanie"
        imap.login(konto["user"], konto["haslo"])
        print(f"{nazwa}: IMAP OK", flush=True)

        etap = "IMAP wybor INBOX"
        status, _ = imap.select("INBOX", readonly=False)

        if status != "OK":
            raise RuntimeError("INBOX niedostepny")

        od_daty = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).strftime("%d-%b-%Y")

        etap = "IMAP wyszukiwanie"
        status, dane = imap.search(
            None, "UNSEEN", "SINCE", od_daty
        )

        if status != "OK":
            raise RuntimeError("Wyszukiwanie IMAP nie powiodlo sie")

        numery = dane[0].split() if dane[0] else []

        print(
            f"{nazwa}: nieprzeczytane: {len(numery)}",
            flush=True
        )

        wyslane = 0
        za_stare = 0

        for num in numery:
            etap = "IMAP pobieranie"
            status, msg_data = imap.fetch(
                num, "(INTERNALDATE BODY.PEEK[])"
            )

            if status != "OK":
                raise RuntimeError("Blad pobierania")

            czesci = [
                x for x in msg_data
                if isinstance(x, tuple) and len(x) >= 2
            ]

            if not czesci:
                raise RuntimeError("Brak danych wiadomosci")

            czas_info = imaplib.Internaldate2tuple(
                czesci[0][0]
            )

            if czas_info is None:
                raise RuntimeError("Brak poprawnej daty IMAP")

            czas = time.mktime(czas_info)

            if time.time() - czas > OKNO_MIN * 60:
                za_stare += 1
                continue

            etap = "Przygotowanie wiadomosci"
            msg = przygotuj_wiadomosc(
                konto, czesci[0][1]
            )

            if smtp is None:
                etap = "SMTP polaczenie"
                print(
                    f"{nazwa}: laczenie SMTP...",
                    flush=True
                )
                smtp = smtplib.SMTP_SSL(
                    konto["smtp"], 465, timeout=30
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
                num, "+FLAGS", r"(\Seen)"
            )

            if status != "OK":
                raise RuntimeError(
                    "Wyslano, ale nie oznaczono jako przeczytane"
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
        pokaz_blad(nazwa, etap, e)
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
