# Trust Centre

A self-hosted portal for compliance reports, certificates and policies. It uses Flask, SQLite, private PDF storage and SMTP. The customer interface is rendered on the server and uses a small script for the agreement dialog.

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

- Sign in with your password and authenticator code, then choose **Manage**. You can also start at `/manage/` to go straight to the dashboard after signing in.
- Under **Settings**, edit the homepage introduction, team description, logo, colours, font, and access-request inbox. Category descriptions, document titles, and teasers are public.
- Under **Accounts**, create customers and choose Basic, Pro, or Expert. Higher tiers include lower tiers. You can enable, disable, delete, or send a password reset from an account page. Owners can also grant administrator access. The server operator uses `enable_totp` to enrol new administrators.
- Under **Documents**, choose **Add document**, complete the access settings, and upload a PDF. The file is a draft until you choose **Publish** under **Versions**. Upload replacements from the same page and use **Restore** to bring back a retained version. Agreement acceptance records are on that document's page.
- Open **Access requests** to review requests, find the customer account, and mark requests as handled. The portal also emails the configured inbox.
- Open **Subscribers** to see subscription status or import a UTF-8 CSV with an `email` column. New addresses receive an opt-in link. Publishing or restoring a version emails confirmed subscribers. Links in those emails still require the applicable access level and agreement.
- Keep branding logos at a stable HTTPS URL. The initial deployment uses local accounts. SSO and self-service payments are future integrations.

## Try the fictional demo

On a fresh installation, run `docker compose exec web python manage.py seed_demo` after the web service starts. For local development, run `python manage.py seed_demo` after migrating. The command loads the Alder Quay Systems homepage with three fictional compliance team profiles and AI-generated portraits. It also publishes two sample PDFs each under Reports, Certificates, and Policies, with a mix of Basic, Pro, and Expert access. The SOC 2 report preview demonstrates the agreement dialog.

Use a separate demo data volume if you also run a real portal. The demo command refuses a database that already has branding or documents and creates no accounts or notification emails. The original PDFs live in `output/pdf/`; the certificate specimens and audit report are clearly marked as fictional.

## Upgrade from the previous Django version

Back up the database and private files together before updating. Keep the existing Compose project name, data volume, `.env`, and any local Compose override. The Flask app uses the existing SQLite records and private PDFs in place. Existing passwords, authenticator enrolments, document revisions, agreements, subscribers, and queued mail carry forward. Users must sign in again and request fresh links for any outstanding password resets. The first startup also works on an empty volume.

## Maintain

- Back up the `portal-data` volume, including `portal.sqlite3` and the `private/` PDFs. Stop both services before taking or restoring a filesystem backup so database and files represent the same point in time. Preserve the `.env` separately.
- Run `./update.sh` from your deployment checkout after backing up. If the checkout predates the script, first run `git pull --ff-only origin main`. The script fast-forwards `main`, rebuilds the image shared by web and mail, restarts the services as needed, and shows their status. The web container initializes the database on startup.
- Check email failures with `docker compose logs mail`. Failed messages retry with backoff. A mail process interrupted immediately after provider acceptance can deliver the same message again.
- Apply dependency and base image updates by rebuilding the image. Take a backup before upgrading.
- Keep the app's data volume off the public web root. Serve only the app's static assets directly.

## Local development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DEBUG=1 MAIL_CONSOLE=1
python manage.py init_db
python manage.py createsuperuser
python manage.py enable_totp YOUR_USERNAME
python manage.py run --host localhost --port 8000
```

Run `python manage.py send_queue --watch` in another shell with the same environment to see queued messages in its console.

Run `DEBUG=1 python -m unittest portal.tests` for the focused workflow checks.
