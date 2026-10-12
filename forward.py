import imaplib, smtplib, email, os, sys, time, html
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
    od = orig.get("From", "?")
    data = orig.get("Date", "?")
    naglowek = f"Od: {od}\nData: {data}\n\n"

    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg["Subject"] = f"[{nazwa}] {orig.get('Subject', '(bez tematu)')}"
    if orig.get("Reply-To"):
        msg["Reply-To"] = orig["Reply-To"]  # odpowiedź w Gmailu pójdzie do nadawcy

    body = orig.get_body(preferencelist=("plain", "html"))
    if body is None:
        msg.set_content(naglowek + "(brak treści)")
    elif body.get_content_type() == "text/html":
        msg.set_content(naglowek + "(wiadomość HTML)")
        info = f"<p><b>Od:</b> {html.escape(str(od))}<br><b>Data:</b> {html.escape(str(data))}</p><hr>"
        msg.add_alternative(info + body.get_content(), subtype="html")
    else:
        msg.set_content(naglowek + body.get_content())

    # przekazujemy tylko oryginalne załączniki (jeśli są), bez pliku .eml
    for zal in orig.iter_attachments():
        dane = zal.get_payload(decode=True)
        if not dane:
            continue
        maintype, subtype = zal.get_content_type().split("/", 1)
        msg.add_attachment(dane, maintype=maintype, subtype=subtype,
                           filename=zal.get_filename() or "zalacznik")

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
