# BlueberryQ local control research

This directory is the MyQ Security 2.0 local-control implementation boundary.
It is independent of BlueberryProxy. BlueberryProxy may provide DNS steering,
but the opener-facing protocol, device credentials, state and Home Assistant
entities belong here.

## Current verified scope

MyQ's public consumer documentation does not describe a local LAN API. Its
integration page describes partnership API access for approved integrations,
and directs homeowners to the myQ app. This repository does not claim an
official local handoff or vendor API access. The HA component below is only a
client for a separately installed and configured MQTT relay.

Home Assistant removed its MyQ cloud integration after the provider blocked
third-party access. For supported local control, Home Assistant recommends
ratgdo, which connects to the opener's Security+ interface and uses its own
ESPHome integration. The official myQ app remains the vendor's remote-control
path. BlueberryQ should be used only with an independently configured,
device-facing relay. It does not replace either provider path.

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
be needed to capture the redirect before the installed app consumes it. No
Amazon or myQ password is collected or stored by BlueberryQ. OAuth tokens and
an optional client secret are stored in HA config entry storage.

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
