import imaplib
import smtplib
import email
import os
import sys
import time
import html
import traceback
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage

OKNO_MIN = 1440  # 24 godziny

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_PASS"]

KONTA = [
    ("Yahoo", "imap.mail.yahoo.com", os.environ["YAHOO_USER"], os.environ["YAHOO_PASS"]),
    ("WP", "imap.wp.pl", os.environ["WP_USER"], os.environ["WP_PASS"]),
]


def przeslij(smtp, nazwa, raw):
    orig = email.message_from_bytes(raw, policy=policy.default)
    od = orig.get("From", "?")
    data = orig.get("Date", "?")
    naglowek = f"Od: {od}\nData: {data}\n\n"

    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg["Subject"] = f"[{nazwa}] {orig.get('Subject', '(bez tematu)')}"

    if orig.get("Reply-To"):
        msg["Reply-To"] = str(orig["Reply-To"])
    elif orig.get("From"):
        msg["Reply-To"] = str(orig["From"])

    body = orig.get_body(preferencelist=("plain", "html"))

    if body is None:
        msg.set_content(naglowek + "(brak tresci)")
    elif body.get_content_type() == "text/html":
        msg.set_content(naglowek + "(wiadomosc HTML)")
        info = (
            f"<p><b>Od:</b> {html.escape(str(od))}"
            f"<br><b>Data:</b> {html.escape(str(data))}</p><hr>"
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

    smtp.send_message(msg)


def main():
    blad = False

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            print("Gmail SMTP: polaczono poprawnie", flush=True)

            for nazwa, host, user, haslo in KONTA:
                imap = None

                try:
                    print(f"--- Konto: {nazwa} ---", flush=True)

                    imap = imaplib.IMAP4_SSL(host, 993, timeout=30)
                    imap.login(user, haslo)
                    print(f"{nazwa}: logowanie IMAP OK", flush=True)

                    status, _ = imap.select("INBOX", readonly=False)
                    if status != "OK":
                        raise RuntimeError("Nie mozna otworzyc INBOX")

                    od_daty = (
                        datetime.now(timezone.utc) - timedelta(days=2)
                    ).strftime("%d-%b-%Y")

                    status, dane = imap.search(
                        None, "UNSEEN", "SINCE", od_daty
                    )
                    if status != "OK":
                        raise RuntimeError("Blad wyszukiwania IMAP")

                    numery = dane[0].split() if dane[0] else []
                    print(f"{nazwa}: nieprzeczytane: {len(numery)}", flush=True)

                    wyslane = 0
                    za_stare = 0

                    for num in numery:
                        status, msg_data = imap.fetch(
                            num, "(INTERNALDATE BODY.PEEK[])"
                        )

                        if status != "OK":
                            raise RuntimeError("Blad pobierania wiadomosci")

                        czesci = [
                            x for x in msg_data
                            if isinstance(x, tuple) and len(x) >= 2
                        ]

                        if not czesci:
                            raise RuntimeError("Brak danych wiadomosci")

                        czas_info = imaplib.Internaldate2tuple(czesci[0][0])
                        if czas_info is None:
                            raise RuntimeError("Nieprawidlowa data IMAP")

                        czas = time.mktime(czas_info)

                        if time.time() - czas > OKNO_MIN * 60:
                            za_stare += 1
                            continue

                        przeslij(smtp, nazwa, czesci[0][1])

                        status, _ = imap.store(num, "+FLAGS", r"(\Seen)")
                        if status != "OK":
                            raise RuntimeError(
                                "Wyslano, ale nie oznaczono jako przeczytane"
                            )

                        wyslane += 1

                    print(f"{nazwa}: za stare: {za_stare}", flush=True)
                    print(f"{nazwa}: przeslano: {wyslane}", flush=True)

                except Exception as e:
                    blad = True
                    print(
                        f"{nazwa}: BLAD: {type(e).__name__}",
                        flush=True
                    )
                    # Szczegoly bledow przechowujemy lokalnie tylko w razie potrzeby;
                    # nie publikujemy danych serwera ani odpowiedzi uwierzytelnienia.
                    if isinstance(e, imaplib.IMAP4.error):
                        print(
                            f"{nazwa}: problem z protokolem IMAP lub logowaniem",
                            flush=True
                        )
                    elif isinstance(e, (OSError, TimeoutError)):
                        print(
                            f"{nazwa}: problem z polaczeniem sieciowym",
                            flush=True
                        )
                    else:
                        print(
                            f"{nazwa}: problem z przetwarzaniem wiadomosci",
                            flush=True
                        )

                finally:
                    if imap is not None:
                        try:
                            imap.logout()
                        except Exception:
                            pass

    except Exception as e:
        blad = True
        print(f"Gmail SMTP: BLAD: {type(e).__name__}", flush=True)

    if blad:
        sys.exit(1)


if __name__ == "__main__":
    main()
