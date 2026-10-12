
import imaplib
import smtplib
import email
import os
import sys
import time
import html

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

    # Nadawca: konto Yahoo lub WP, a nie Gmail
    msg["From"] = formataddr((
        konto["wyswietlana_nazwa"],
        konto["user"]
    ))

    msg["To"] = GMAIL_USER
    msg["Subject"] = temat

    # Odpowiedzi kieruj do oryginalnego nadawcy
    reply = orig.get("Reply-To") or orig.get("From")
    if reply:
        msg["Reply-To"] = str(reply)

    naglowek = (
        f"Wiadomosc przekazana ze skrzynki: "
        f"{konto['nazwa']}\n"
        f"Oryginalny nadawca: {od}\n"
        f"Data: {data}\n\n"
    )

    body = orig.get_body(
        preferencelist=("plain", "html")
    )

    if body is None:
        msg.set_content(naglowek + "(brak tresci)")

    elif body.get_content_type() == "text/html":
        msg.set_content(
            naglowek + "(wiadomosc HTML)"
        )

        info = (
            f"<p><b>Skrzynka:</b> "
            f"{html.escape(konto['nazwa'])}<br>"
            f"<b>Od:</b> {html.escape(od)}<br>"
            f"<b>Data:</b> {html.escape(data)}</p><hr>"
        )

        msg.add_alternative(
            info + body.get_content(),
            subtype="html"
        )

    else:
        msg.set_content(
            naglowek + body.get_content()
        )

    # Zachowaj oryginalne zalaczniki
    for zal in orig.iter_attachments():
        dane = zal.get_payload(decode=True)

        if dane is None:
            continue

        maintype, subtype = (
            zal.get_content_type().split("/", 1)
        )

        msg.add_attachment(
            dane,
            maintype=maintype,
            subtype=subtype,
            filename=zal.get_filename() or "zalacznik"
        )

    return msg


def obsluz_konto(konto):
    nazwa = konto["nazwa"]
    imap = None
    smtp = None

    try:
        print(f"--- Konto: {nazwa} ---", flush=True)

        # Polaczenie z poczta przychodzaca
        imap = imaplib.IMAP4_SSL(
            konto["imap"], 993, timeout=30
        )

        imap.login(konto["user"], konto["haslo"])
        print(f"{nazwa}: IMAP OK", flush=True)

        status, _ = imap.select(
            "INBOX", readonly=False
        )

        if status != "OK":
            raise RuntimeError("Nie mozna otworzyc INBOX")

        od_daty = (
            datetime.now(timezone.utc)
            - timedelta(days=2)
        ).strftime("%d-%b-%Y")

        status, dane = imap.search(
            None, "UNSEEN", "SINCE", od_daty
        )

        if status != "OK":
            raise RuntimeError("Blad wyszukiwania IMAP")

        numery = dane[0].split() if dane[0] else []

        print(
            f"{nazwa}: nieprzeczytane: {len(numery)}",
            flush=True
        )

        wyslane = 0
        za_stare = 0

        for num in numery:
            status, msg_data = imap.fetch(
                num, "(INTERNALDATE BODY.PEEK[])"
            )

            if status != "OK":
                raise RuntimeError(
                    "Blad pobierania wiadomosci"
                )

            czesci = [
                x for x in msg_data
                if isinstance(x, tuple) and len(x) >= 2
            ]

            if not czesci:
                raise RuntimeError(
                    "Brak danych wiadomosci"
                )

            czas_info = imaplib.Internaldate2tuple(
                czesci[0][0]
            )

            if czas_info is None:
                raise RuntimeError(
                    "Nieprawidlowa data IMAP"
                )

            czas = time.mktime(czas_info)

            if time.time() - czas > OKNO_MIN * 60:
                za_stare += 1
                continue

            # Polaczenie SMTP dopiero przy nowej poczcie
            if smtp is None:
                smtp = smtplib.SMTP_SSL(
                    konto["smtp"], 465, timeout=30
                )

                smtp.login(
                    konto["user"],
                    konto["haslo"]
                )

                print(
                    f"{nazwa}: SMTP OK",
                    flush=True
                )

            msg = przygotuj_wiadomosc(
                konto, czesci[0][1]
            )

            # Wysylanie z Yahoo albo WP do Gmail
            smtp.send_message(
                msg,
                from_addr=konto["user"],
                to_addrs=[GMAIL_USER]
            )

            # Oznacz jako przeczytane po wysylce
            status, _ = imap.store(
                num, "+FLAGS", r"(\Seen)"
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
        print(
            f"{nazwa}: BLAD: {type(e).__name__}",
            flush=True
        )

        if isinstance(
            e, (imaplib.IMAP4.error, smtplib.SMTPException)
        ):
            print(
                f"{nazwa}: problem z logowaniem "
                "lub protokolem pocztowym",
                flush=True
            )
        elif isinstance(e, (OSError, TimeoutError)):
            print(
                f"{nazwa}: problem sieciowy",
                flush=True
            )
        else:
            print(
                f"{nazwa}: problem z przetwarzaniem",
                flush=True
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
