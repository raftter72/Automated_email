import imaplib, smtplib, email, os, sys, time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage

OKNO_MIN = 60  # ile minut wstecz patrzymy na nowe maile

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_PASS"]

KONTA = [
    ("Yahoo", "imap.mail.yahoo.com", os.environ["YAHOO_USER"], os.environ["YAHOO_PASS"]),
    ("WP", "imap.wp.pl", os.environ["WP_USER"], os.environ["WP_PASS"]),
]


def przeslij(smtp, nazwa, raw):
    orig = email.message_from_bytes(raw, policy=policy.default)
    naglowek = f"Od: {orig.get('From', '?')}\nData: {orig.get('Date', '?')}\n\n"

    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg["Subject"] = f"[{nazwa}] {orig.get('Subject', '(bez tematu)')}"

    body = orig.get_body(preferencelist=("plain", "html"))
    if body is None:
        msg.set_content(naglowek + "(brak treści)")
    elif body.get_content_type() == "text/html":
        msg.set_content(naglowek + "(wiadomość HTML)")
        msg.add_alternative(body.get_content(), subtype="html")
    else:
        msg.set_content(naglowek + body.get_content())

    msg.add_attachment(raw, maintype="application", subtype="octet-stream",
                       filename="oryginal.eml")
    smtp.send_message(msg)


def main():
    blad = False
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_USER, GMAIL_PASS)

        for nazwa, host, user, haslo in KONTA:
            try:
                imap = imaplib.IMAP4_SSL(host)
                imap.login(user, haslo)
                imap.select("INBOX")

                wczoraj = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%d-%b-%Y")
                _, dane = imap.search(None, "UNSEEN", "SINCE", wczoraj)
                numery = dane[0].split()
                wyslane = 0

                for num in numery:
                    _, msg_data = imap.fetch(num, "(INTERNALDATE BODY.PEEK[])")
                    czas = time.mktime(imaplib.Internaldate2tuple(msg_data[0][0]))
                    if time.time() - czas > OKNO_MIN * 60:
                        continue  # za stary, pomijamy
                    przeslij(smtp, nazwa, msg_data[0][1])
                    imap.store(num, "+FLAGS", "\\Seen")  # oznacz dopiero po wysłaniu
                    wyslane += 1

                print(f"{nazwa}: przesłano: {wyslane}")
                imap.logout()
            except Exception as e:
                blad = True
                print(f"{nazwa}: BŁĄD: {type(e).__name__}")  # bez szczegółów, żeby nie zdradzać danych

    if blad:
        sys.exit(1)  # GitHub wyśle maila, że coś poszło nie tak


if __name__ == "__main__":
    main()
