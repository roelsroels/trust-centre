# Trust Centre

A self-hosted portal for compliance reports, certificates and policies. It uses Django, SQLite, private PDF storage and SMTP. The customer interface is rendered on the server and uses a small script for the agreement dialog.

## Run behind a reverse proxy

1. Copy `.env.example` to `.env`. Generate a secret with `openssl rand -hex 48` and set `SECRET_KEY`. Set `BASE_URL` to your public HTTPS origin and configure SMTP.
2. Run `docker compose up -d --build`.
3. Run `docker compose exec web python manage.py createsuperuser` to create the first Owner.
4. Run `docker compose exec web python manage.py enable_totp YOUR_USERNAME`. Add the private setup URI to an authenticator that accepts TOTP setup links, or enter its `secret` manually. Keep terminal output private. Use the same command to reset a lost authenticator.
5. Configure your HTTPS reverse proxy to forward to `127.0.0.1:8000` on the Docker host.

Example Nginx directives inside the HTTPS virtual host:

```nginx
client_max_body_size 27m;
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
}
```

Redirect HTTP to HTTPS at the proxy. Keep the web service private behind the proxy, overwrite the forwarded headers as shown, and keep proxy caching disabled. The application trusts these headers for HTTPS and sign-in throttling. Run one web worker and one mail worker per installation.

## Configure the portal

- Open `/manage/`. In **Portal settings**, set your company name, introduction, short team description, logo URL, palette, font and access-request email address. Put only information suitable for public display in category descriptions, document titles and teasers.
- Under **Accounts**, create customer accounts and assign Basic, Pro or Expert. A higher tier includes lower tiers. Customers can use the password reset link to choose their password after you create an account with a strong temporary password. Admins can disable, delete, or reset customer accounts. To create an Admin, an Owner creates an account and enables its administration flag. The server operator runs `enable_totp` for that account.
- Under **Documents**, choose a category, minimum tier and optional agreement, then attach a PDF with a revision label. Save, open the draft revision from the version history and select **Publish or restore**. Subsequent uploads remain drafts until published. The retention field controls the number of previous published files kept in addition to the live file. Lowering it removes excess prior files.
- Customers can request access from a locked document. The portal queues an email to the configured inbox and records the request in the admin console. An admin grants the tier manually and marks the request handled.
- Customers can opt in under **Email updates**. In **Subscribers**, import a UTF-8 CSV with an `email` column to queue invitations. Publishing and restoring a revision queues one email per confirmed subscriber. Emails contain the approved teaser, a link to the document and an unsubscribe link. Document access and agreement checks still apply. Changing the agreement text requires fresh acceptance of that text.
- Keep branding logos at a stable HTTPS URL. The initial deployment uses local accounts. SSO and self-service payments are future integrations.

## Try the fictional demo

On a fresh installation, run `docker compose exec web python manage.py seed_demo` after the web service starts. For local development, run `python manage.py seed_demo` after migrating. The command loads the Alder Quay Systems homepage with three fictional compliance team profiles and AI-generated portraits. It also publishes two sample PDFs each under Reports, Certificates, and Policies, with a mix of Basic, Pro, and Expert access. The SOC 2 report preview demonstrates the agreement dialog.

Use a separate demo data volume if you also run a real portal. The demo command refuses a database that already has branding or documents and creates no accounts or notification emails. The original PDFs live in `output/pdf/`; the certificate specimens and audit report are clearly marked as fictional.

## Maintain

- Back up the `portal-data` volume, including `portal.sqlite3` and the `private/` PDFs. Stop both services before taking or restoring a filesystem backup so database and files represent the same point in time. Preserve the `.env` separately.
- Check email failures with `docker compose logs mail`. Failed messages retry with backoff. A mail process interrupted immediately after provider acceptance can deliver the same message again.
- Apply dependency and base image updates by rebuilding the image. Take a backup before upgrading and periodically run `docker compose exec web python manage.py clearsessions`.
- Keep the app's data volume off the public web root. Serve only the app's static assets directly.

## Local development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DEBUG=1 EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
python manage.py migrate
python manage.py createsuperuser
python manage.py enable_totp YOUR_USERNAME
python manage.py runserver localhost:8000
```

Run `python manage.py send_queue --watch` in another shell with the same environment to see queued messages in its console.
