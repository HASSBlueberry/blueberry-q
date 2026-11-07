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
