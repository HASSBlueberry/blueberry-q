# OAuth and App Check renewal

Integration 0.8.0 can import OAuth credentials from BlueberryQDB. Start manual
MyQ sign-in in the QDB panel, finish verification, and copy the entire
`com.myqops://android?...` callback URL into the panel. QDB exchanges the code
and stores the access and refresh tokens. During BlueberryQ setup, enter or
select the QDB URL and key, then choose Sync OAuth tokens from BlueberryQDB.
When a bridge is configured, QDB owns OAuth refresh and BlueberryQ syncs the
current credentials through `/v1/oauth/tokens`. This avoids competing refresh
requests. Home Assistant diagnostic sensors report token expiry and sync
times without exposing token values as entity attributes.

BlueberryQDB 0.4.0 keeps App Check in private persistent storage, opens MyQ
near expiry, and polls its process logs for up to 60 seconds. The panel shows
expiry and recovery state and can explicitly reveal the usable token for
60 seconds. Do not post that token in support messages.

Optional login recovery is disabled by default. In the Home Assistant app
Configuration tab, set `login_recovery_enabled`, `myq_email`, `myq_password`,
`imap_enabled`, `imap_host`, `imap_port` (993), `imap_username`,
`imap_password` and `imap_folder` (INBOX). Use the mailbox provider's supported
IMAP credential or app password. Recovery only reads mail over verified TLS.
It does not mark messages read, delete mail, or send mail. IMAP credentials
are stored in Home Assistant app options, so restrict access to configuration
and backups. Never paste credentials into chat.

The bridge records a mailbox baseline before acting on the phone. It accepts
only new messages for the configured MyQ email, from notification@myq.com,
with the exact six-character verification subject. Recovery stops after
three minutes or on an unknown, locked or obstructed screen. It submits
credentials and a verification code at most once per attempt. It can select
an explicitly labelled email delivery button. Prefilled fields that differ
from the configured values must be cleared on the phone first. UI text input
currently supports printable ASCII without spaces or percent characters.
Automatic recovery has a 30-minute cooldown. It signs out through the app's
visible profile screen before signing in again. The app must actually issue
and log a fresh App Check token for recovery to succeed. A fresh-token path
has not been validated on the real login screen.

ADB targets may point to an externally managed Android environment. This
release does not package ReDroid, Magisk, Zygisk or an integrity provider.
The current host has no Android Binder devices, and an attestation-capable
emulator has not been established. Do not assume an emulator will renew
App Check successfully.

Cloud status is read every 30 seconds and several more times during the
30 seconds after a command. The integration reports provider states and
never treats successful command delivery as confirmation of movement.
Download diagnostics to see allowlisted state values and provider timestamps.
On the live account inspected during development, MyQ returned a garage door
with closed state, matching the official app's Closed for 4 hours screen.
The response contained no light state or capabilities. Separate devices with
light_state are now retained for light sensors when supplied by MyQ, but
light actuation and this opener's light availability remain unverified.
No physical garage command was sent during development.

## Earlier releases and research

# Blueberry MyQ 0.7.0

Native phone mode uses the already signed-in official MyQ app through
BlueberryQDB 0.3.0. It needs no separate Chrome login, OAuth token, App Check
or client secret. In the QDB panel, select the phone, show the MyQ door list,
press Check connection now and save the actual door control. In HASS, choose
Use the signed-in MyQ app through BlueberryQDB and submit its connection form.
There is no timed OAuth step in this mode.

Keep the phone unlocked and MyQ visible. Commands are guarded by fresh UI
state and a calibrated semantic selector. They are not blind toggles. Unknown
states, confirmations, moving doors and changed controls are refused. HASS
reports what the app displays rather than assuming a gesture moved the door.
The tested calibration and gesture flows use synthetic screens. Physical door
operation and the current UI on the HASS-connected phone remain unverified.

Native and cloud entries are separate. After verifying native mode, disable
the old cloud entry to avoid duplicate opener controls. If the phone needs a
new sign-in, do it inside MyQ, then check the native connection again.

The previous cloud OAuth, App Check, and independent MQTT research remains
below for those paths.

> [!CAUTION]
> MyQ can block this method entirely with one update that enforces Firebase App Check.
> This is the last bastion of local control for MyQ Security 2.0 openers or
> hubs, made in hopes
> to convince them that I don't aim to back off or "abuse" or "compromise"
> their service (code-word for "we want to keep our proprietary control of the devices
> you own"). If they enforce App Check, this integration will stop working when
> they (or you) rotate your keys. MyQ/Chamberlain Group is untrustable
> and unpredictable. Don't expect this to function in a year's time.

# BlueberryQ local control research

This directory is the MyQ Security 2.0 local-control implementation boundary.
It is independent of BlueberryProxy. BlueberryProxy may provide DNS steering,
but the opener-facing protocol, device credentials, state and Home Assistant
entities belong here.

## Simplified Home Assistant setup

Version 0.4 starts with myQ email/password and follows the provider's login forms with an isolated cookie session. If the provider returns a verification-code form, HA opens a separate verification step. Delivery-method forms can open a method selector first. Passwords and codes are not saved. Cookies and CSRF fields stay in the same temporary session across prompts, incorrect codes can be retried, and the session closes on success, flow cancellation or ten minutes of inactivity.

The browser sign-in option is available on the login form, including Amazon when offered by myQ. JavaScript-only verification, CAPTCHA and external identity-provider challenges use this browser path. These cannot be handled as a generic OTP form. The full native callback URL must be copied back into the same HA setup dialog. Amazon Key delivery linking is separate.

Callbacks validate the registered URI, state, PKCE verifier and optional issuer. A callback from another flow cannot be exchanged without that flow's verifier. Start a fresh sign-in after installing an update or restarting HA. Invalid callbacks retain the current state and link, and only the explicit fresh-sign-in option replaces them. Token errors distinguish an expired/used code from rejected client credentials. Provider error descriptions and callback codes are not logged. Advanced setup retains optional OAuth client settings and existing MQTT relay configuration.

After token exchange, device discovery has its own retry step. A temporary account API failure does not redeem the authorization code a second time. API access denial is shown separately from credential errors. Later account refreshes add supported garage door covers automatically. Other myQ device families do not yet have entity implementations.

Provider form handling is tested with simulated password, verification-code, delivery-method and callback exchanges, plus flow cancellation and discovery retries. The current live provider login page was checked for form and CSRF compatibility. Authenticated sign-in and MFA have not been certified with a live account, and the provider may reject private application API access.

## Current verified scope

MyQ's public consumer documentation does not describe a local LAN API. Its
integration page describes partnership API access for approved integrations,
and directs homeowners to the myQ app. This repository does not claim an
official local handoff or vendor API access. The integration includes an experimental client for private application API
endpoints and a client for an independently configured MQTT relay.

Home Assistant removed its MyQ cloud integration after the provider blocked
third-party access. For supported local control, Home Assistant recommends
ratgdo, which connects to the opener's Security+ interface and uses its own
ESPHome integration. The official myQ app remains the vendor's supported remote-control path.
BlueberryQ cloud mode is experimental and is not endorsed by the vendor.
MQTT mode requires a separate device-facing relay.

The available public analysis covers one board and firmware combination,
Chamberlain logic board `050DCTWF` running firmware `3.13`. That device connects
to `connect.myqdevice.com` using MQTT 3.1.1 over TLS 1.2 with
`PSK-AES128-CBC-SHA`. Its TLS identity and 16-byte pre-shared key are unique to
the device and were recovered from that device's SPI flash. These values are
not universal constants and are not derivable from the Android app package.

The MQTT command topic is `G/<serial>/011/0000`. The 12-byte command payload
is six bytes of opener ID, `80 02 00 01`, then a little-endian 16-bit action.
`01 00` means open and `00 00` means close. Telemetry is published to
`S/0F/<serial>/011/0000`. The final little-endian value has been observed as
1 open, 2 closed, 4 opening and 5 closing. Value 3 stopped is reported as
unverified by the source analysis.

This is a local relay protocol once the bridge's device-specific TLS PSK and
opener ID are available. The new `custom_components/blueberry_q` integration
exposes MQTT telemetry as a Home Assistant garage-door cover and publishes the
recovered open/close frames to the device command topic. It depends on the Home
Assistant MQTT integration and a device-facing TLS-PSK relay that forwards
these topics to the HA broker. This package does not implement or configure
that listener, so it is usable only when that separate relay is already
running. The device also expects a 32-byte response on
`G/<serial>/012/0040` soon after its startup announce. The response semantics
are not established, so this project does not fabricate that response or claim
that the device can complete startup yet. Capturing the 32-byte exchange is the
next implementation gate.

## Next RE steps

The MyQ app artifact in this workspace contains service-host and account
configuration, but no raw-flash export endpoint. The device's current remote
APIs do not provide the SPI image needed to recover the per-device PSK. The
available analysis project likewise requires an independently acquired flash
image. Firmware update packages, if exposed, would not be a full flash dump.

1. Extract PSM records from a legally obtained flash dump and document the
   serial, wrapped `myq_aes`, and opener ID locations without checking secrets
   into the repository.
2. Recover the per-device PSK and verify the unwrap against a local capture.
3. Capture startup topic `S/0F/<serial>/003/0000` and response exchange on
   `G/<serial>/012/0040`.
4. Implement the TLS-PSK MQTT listener and HA MQTT discovery after startup is
   reproducible. Keep support claims scoped to tested board and firmware pairs.

No firmware dump or physical MyQ bridge is present in this workspace, so this
repository does not claim to have recovered a PSK, startup response, or working
door control for the user's hardware.

## Sources

The initial protocol lead is the independent
[MyQ Security 2.0 Home Assistant research](https://github.com/StanleyCA/MyQ-Security2.0-HomeAssistant),
including its writeup. Its results apply to its examined board and firmware,
not to every MyQ opener.

## Experimental application API mode

Version 0.2 adds an application OAuth mode with PKCE, state validation, token
refresh, account and garage opener enumeration, and Home Assistant covers for
open, close, and reported door state. MQTT mode remains available for an
existing local relay. Cloud mode does not require the HA MQTT integration.

Choose provider sign-in in setup. Enter the OAuth client details, then use the
linked provider page to sign in with username/password. Use Amazon sign-in if
the provider offers it on that page. Copy the full registered callback URL
into setup to finish authorization. This is currently a manual callback
exchange, including for application deep links. Browser developer tools may
be needed to capture the redirect before the installed app consumes it. Browser sign-in keeps the account password on the provider page. The simple
email/password flow sends it once to the provider login form and does not
persist it. OAuth tokens and
an optional client secret are stored in HA config entry storage.

Setup reports identity connection, TLS, timeout, rate limit, server, and HTTP rejection failures separately. If password sign-in is blocked by a provider challenge, select browser sign-in. Once authorization completes, discovery reports device API connectivity and authorization failures separately. These messages intentionally omit callback values and provider response bodies.

The application API is undocumented and this integration is not endorsed by
MyQ. Some clients require a secret, and the provider can refuse third-party
traffic. A failed sign-in, provider block, or empty supported device list does
not create a working config entry. Requests use normal TLS validation. Door
commands are sent once, without retries or optimistic state updates. Runtime
401 errors require signing in again. Amazon Key linking remains a separate
in-garage delivery service, not a general control API for this integration.

This implementation has not been exercised against a live account. Earlier
research notes above describe the independent MQTT path and do not describe
the cloud adapter. Public app client identifiers are defaults, not a claim
that the provider accepts public-client token exchange without a secret.

The token endpoint can return a proprietary JSON rejection with code `401.122` and description `The application is unauthorized`, rather than an OAuth `error` field. Version 0.4.2 identifies this application rejection explicitly. A diagnostic request with an invalid code reproduced it, without using account credentials. Browser login success does not establish that the token endpoint authorizes this client. Live account token exchange remains unverified.

## Application configuration extraction

Version 0.4.3 reads `assets/myq-config.json` from a local APK or the base APK inside an APKS archive during browser setup. Put the downloaded archive at `/config/.storage/blueberry_q/myq.apks` or enter its path. Extraction runs in HA's executor, uses bounded archive reads, selects the exact Production environment, and reads client ID, redirect URI, scope and a client secret only if the credentials provider contains one. It does not execute application code or log the extracted settings. The archive is not bundled into the integration package.

The downloaded 5.323.0.1132341 archive contains client ID, redirect URI and scope but no secret in its production credentials provider. Inspection of classes3.dex and classes6.dex found the token interface in `po0/s.java`, which includes a `/connect/token` method accepting `Firebase-AppCheck-Token`. Other overloads omit this header, so this observation alone does not establish which path caused a particular rejection. Runtime attestation is not a static application secret. No valid attestation or successful account token exchange has been recovered. Extraction currently reproduces the app's published OAuth configuration and does not establish that error 401.122 is resolved.

Further token construction tracing found `po0/k.E` builds the authorization-code form with client ID, scope, grant type, code, redirect URI, and verifier. BlueberryQ now includes the extracted scope in the token form too. That method selects an exchange without App Check when unconfigured, an error-marker header when retrieval fails, or the acquired runtime token when available. This establishes conditional attestation handling, not that a particular account rejection proves attestation enforcement. No static client secret is passed by this method.

The [Android authentication investigation](research/android-auth.md) records
the matching installed APK, production Play Integrity setup, release cache
behavior, and native refresh construction. Refresh now omits the code-exchange
scope and redirect fields, following the traced interceptor. This is verified
offline only. The phone's existing session was preserved, and independent
authenticated cloud access remains unverified.

Runtime follow-up found App Check acquisition and successful native OAuth
responses in retained process logs. A controlled test using the same synthetic
invalid grant returned `401.122` without the observed runtime token and
`invalid_grant` with it. This provides evidence that the runtime token passes
the application check for that request. No account access token was obtained.
See the [runtime follow-up](research/android-auth.md#runtime-observation-follow-up)
and its collector for sanitized evidence and observation limits.

Live follow-up completed an independent browser sign-in using the pinned
runtime App Check token. BlueberryQ exchanged its own callback and PKCE
verifier, obtained access and refresh tokens, and discovered one supported
garage door. A subsequent refresh succeeded, rotated the refresh token, and
device discovery succeeded again. No door command was sent. Credentials are
stored privately on the host outside this repository. This verifies cloud
authentication and discovery for this account at the time of the test, rather
than a complete deployed HA setup or physical door control. See the
[live validation report](research/live-auth-validation.json).

## BlueberryQDB app and credential migration

Version 0.5.0 can fetch App Check from the separate
[BlueberryQDB Home Assistant app](../BlueberryQDB/README.md). That app packages
ADB, discovers authorized USB phones, persists debugging keys and caches
runtime tokens. Supervisor discovery supplies its private API connection to
MyQ setup. The integration still contacts MyQ directly for OAuth and devices.

Setup and Reconfigure also accept an App Check token, existing OAuth access
and refresh tokens, expiry time and optional OAuth client settings. A supplied
refresh token is refreshed before discovery and entry creation. A bridge URL
and key take priority over a pasted token; turn off Use BlueberryQDB to switch
back to manual attestation. Existing version 1 entries migrate to version 2
without changing their account identity or entity IDs.

The [offline migration helper](../../scripts/migrate-blueberryqdb.py) prepares
private BlueberryQDB state and integration fields from a recovered credential
file. It does not write Home Assistant storage or publish credentials. See
[installation and migration instructions](../BlueberryQDB/DOCS.md). Source
workspace paths in these links refer to the full BlueberryFaygo checkout.

A live test using the private migrated App Check cache through BlueberryQDB's
authenticated API completed OAuth refresh and discovered one garage door.
The container starts with packaged ADB and persistent keys. Phone discovery
was verified on the host, but the phone's retained logs had rotated past its
acquisition event, so a fresh extraction could not be repeated in that run.
Home Assistant OS USB pass-through and Supervisor Ingress remain to be tested
on the target host. No door commands were sent.

## Home Assistant token-exchange rejection fixed in 0.5.1

A controlled live comparison reproduced the generic rejection when requests
inherited a HomeAssistant User-Agent. With identical OAuth fields and the
same valid App Check token, that agent received HTTP 403 without an OAuth
error. An explicit `BlueberryQ/0.5.1` agent received the expected HTTP 400
`invalid_grant` for the synthetic invalid code. The integration now overrides
the agent on MyQ identity and device requests without changing Home
Assistant's shared session. A real refresh and discovery succeeded using a
session whose default agent identified itself as Home Assistant.

The callback dialog now shows a sanitized rejection diagnostic, including
HTTP status, allowlisted OAuth error, numeric provider code, and App Check
source. Provider bodies, descriptions, callback values and tokens are not
logged. Manual token preflight accepts a bare JWT, a JSON-quoted JWT, or the
complete Firebase-AppCheck-Token header line. It rejects malformed candidates,
wrong issuer metadata, and expired candidates before sending the OAuth code.
These local metadata checks do not verify the JWT signature. A replacement
manual token can be supplied in the callback dialog without changing the
flow's state or PKCE verifier.

After installing the update, restart Home Assistant and start a fresh sign-in,
since restarting discards any pending flow's verifier. The
[sanitized live validation](research/hass-user-agent-validation.json) records
the request comparison and successful refresh/discovery. No door command was
sent.

## Sign in through the connected phone

Integration 0.6.0 and BlueberryQDB 0.2.0 add automatic phone-browser sign-in.
Choose Sign in on connected phone with BlueberryQDB during setup, confirm the
discovered connection, and finish any provider prompts in Chrome on the phone.
Home Assistant handles callback capture, PKCE exchange, account tokens and
discovery automatically. No pre-existing access token or OAuth client secret
is required for the tested Android client. A manual App Check token can
optionally seed the bridge cache if runtime logs have rotated.

Existing entries can select phone sign-in from Reconfigure, and configured
bridge entries use it for reauthentication. The callback and verifier belong
to the same HASS flow. QDB receives no verifier or account tokens. The progress
dialog can be cancelled to release the transient browser session. Passwords
and MFA stay on the provider's phone page. Packaged end-to-end behavior still
requires validation on the HASS-connected phone.

## Phone timing and App Check fixes in 0.6.1

Use BlueberryQDB 0.2.1 with integration 0.6.1. Chrome preparation no longer
uses the human authentication window. The user receives 15 minutes once the
browser is ready. Valid App Check material is retained across preflight and
exchange, expired material is refreshed through the bridge, and callback
delivery no longer waits for slow browser cleanup. The app tries native MyQ
refresh activity without interrupting a managed Chrome login.

The phone form no longer presents No token response recorded as if it were an
authentication failure. Errors before OAuth exchange identify the actual phase
and safe reason, while successful exchange is reported separately from device
discovery. Regression tests include delayed callbacks beyond command timeout,
separate startup/user budgets, token reuse during bridge outages, and stale
log messages that must not overwrite a newer cache. Target-host behavior still
requires live verification; these changes do not prove the exact failure in
every phone/Chrome combination has been reproduced.
