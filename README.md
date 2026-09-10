# UGREEN Connect Plus for Home Assistant

Home Assistant integration for chargers managed by the **UgreenConnect** app
(`*.ugreeniot.com`). Developed against a **UGREEN Nexode Pro 300W (X783)** —
sold as the *Nexode Pro Smart Display Desktop Charger, 300W, 8-Port, GaN*.
`X783` is the code the app knows it by, and the one that turns up in entity ids;
it is not printed on the box.

Live per-port **voltage, current and power**, plus everything the app's screen
settings can do: brightness, screen-off time, charging mode, and the whole
screensaver — clock style, time format and wallpaper, your own included.

![The charger on a dashboard](docs/dashboard.png)

*That dashboard is [`docs/dashboard.yaml`](docs/dashboard.yaml), ready to paste
and built only from cards Home Assistant ships with. The screenshot predates the
sections for charging, the energy counters and the custom mode's port
allocation; those are in the file.*

> Not affiliated with, endorsed by, or supported by UGREEN. Trademarks belong to
> their respective owners.

## Where this comes from

A fork of [s1mptom/ugreen_connect](https://github.com/s1mptom/ugreen_connect)
by Pavel Turbin, who did the part that matters most: reading the charger's own
binary protocol out of the app's traffic, along with the account API and the
gateway that carries it. That decoding is his. It stays under his MIT licence
and his copyright notice stays in this repository's LICENSE.

This runs under its own domain, `ugreen_connect_plus`, so both can be
installed at once and neither disturbs the other's entities.

What it adds on top of 0.17.0:

- **The custom charging mode, read in full.** Per-port wattage limits, the
  shared C6+A setting, and which fast-charge protocols each group may
  negotiate. Its name comes from the account, matched against the figures the
  charger itself reports rather than assumed.
- **A device per port.** A port answers for itself instead of contributing six
  rows to a list of fifty.
- **A charging flag per port**, taken from the session tracker rather than
  from a wattage threshold, which on this charger cannot tell a full battery
  from a bare cable.
- **Lifetime energy counters**, per port and for the charger, ready for the
  Energy dashboard without a Riemann helper.
- **Events when a bout of charging starts and finishes**, carrying its
  watt-hours, duration, peak and protocol.
- **The age of the readings**: a timestamp of the last poll that came back
  whole, which stays available while polls are failing.
- **Polling that follows the work**, backing off six-fold while nothing is
  charging -- roughly 17,000 requests a day become a fraction of that.
- **The charging mode reported** rather than shown as `unknown` whenever the
  charger sits in a mode this cannot set.
- **A setup dialog that only asks for what it keeps.** Three of its fields
  were collected and then quietly thrown away; they are tuning with sensible
  defaults, and the options is where they always did their work.
- **Other chargers than the one it was written on.** The port list comes from
  the charger's own report rather than from a constant, so a model nobody here
  has ever seen still gets an entity for every port it reports; `productNo`
  supplies the names where they are known and numbers them where they are not.
  The 160 W X776's names were confirmed by its owner against the hardware. Its
  custom mode is left alone rather than guessed at: that parameter block's
  shape is the X783's.
- **A German translation.**

The fixes were offered upstream first, as
[PR #3](https://github.com/s1mptom/ugreen_connect/pull/3) and
[issue #4](https://github.com/s1mptom/ugreen_connect/issues/4).

## Supported chargers

| Model | Readings | Port names | Screen settings | Custom mode |
| --- | --- | --- | --- | --- |
| **X783** — Nexode Pro 300W, 8-port | yes | yes | yes | yes |
| **X776** — Nexode Pro 160W | yes | yes | most, some read-only | no |
| anything else | yes, numbered `P1`… | no | no | no |

Readings work on any of them, because how many ports a charger has can be
counted from the length of the report it sends. The rest cannot be counted,
and is not guessed at.

Port *names* come from `productNo`, which is what the account API calls the
model. The X783's were read off the app's own port table and checked against
the hardware. The X776's were settled the same way, by
[its owner](https://github.com/s1mptom/ugreen_connect/issues/2): devices were
plugged into the built-in cable and into C2, and the raw frames showed those
two slots carrying voltage and the other two at rest. Its readings are
regression-tested against those frames.

The **screen settings** are taken one field at a time rather than one model at
a time. The X776's were mapped on a live one, a single change at a time:
brightness, the screen timeout and the charging mode sit exactly where the
X783's do, and the screensaver, clock style, time format and current wallpaper
follow nine bytes earlier, because the parameter block between them is shorter.
Its wallpaper *library* is not claimed — the byte where the X783 counts one
reads 5 there whether three pictures follow or four.

Reading a field is not permission to write it. Brightness is set by a command
carrying one byte, so knowing where to read it is knowing how to set it; the
charging mode's command carries the whole parameter block, whose length differs
between models. Fields in that position are shown and refuse to be set, with a
message saying so, rather than being hidden or written on a guess. Each of them — brightness, screen-off time,
charging mode, screensaver, wallpaper — is a byte offset in the state reply,
established
by setting a value in the app and watching which byte moved. On a charger
whose reply is laid out differently they would read something plausible and
wrong, and they write back as well as read. So they appear only for a model
whose reply has actually been read on hardware. The **custom mode** is
withheld for the same reason, its parameter block being the X783's shape.

The integration says as much itself: an unfamiliar model is logged once, with
what it gets, what it does not, and what would change that — a diagnostics
download on the issue tracker, which is all it takes to add a model to the
table.

## Why it exists

The charger has **no local API at all** — a full TCP 1–65535 scan finds nothing
open, and there is no mDNS or SSDP. Its only outbound path is its own cloud.
Local control exists solely over BLE. So a cloud integration is the only way to
get readings into Home Assistant without a Bluetooth proxy next to the device.

## Coming from the original

Remove `ugreen_connect` before adding this one. Both can run side by side --
that is what the separate domain is for -- but Home Assistant hands out entity
ids first come, first served, so with the original still installed everything
here arrives as `..._power_2` and stays that way. Removing it afterwards does
not undo the suffix.

The history does not carry across either way: entity ids are the same, but
`unique_id` includes the domain, so Home Assistant treats these as new
entities. There is no migration for that, and pretending otherwise would only
be a way of losing the old data twice.

## Install

**HACS** → three-dot menu → *Custom repositories* → add `lukislp/ugreen_connect_plus`
as type *Integration* → install → **restart Home Assistant** → *Settings →
Devices & Services → Add integration → UGREEN Connect Plus*.

Manual: copy `custom_components/ugreen_connect_plus` into your `config/` and restart.

Sign in with your normal UGREEN account e-mail and password, and pick the region
your account belongs to — the same one the app shows. Accounts are not shared
between regions.

Requires Home Assistant **2024.7** or newer.

## What you get

Entity ids below are written `<device>`; in practice that is the charger's name,
e.g. `sensor.ugreen_nexode_pro_x783_c1_power`.

### Readings

| Entity | Notes |
|---|---|
| `sensor.<device>_c1_power` … `_dc_power` | one per port: C1–C6, A1, DC |
| `sensor.<device>_c1_voltage`, `_c1_current` | same ports |
| `sensor.<device>_c1_protocol` | negotiated fast-charge protocol: PD, PPS, QC, AFC, FCP, UFCS, AVS |
| `sensor.<device>_c1_session_energy` | watt-hours delivered to whatever is plugged into that port now |
| `sensor.<device>_c1_session_charge` | the same session read as milliamp-hours into a battery |
| `sensor.<device>_total_power` | sum across ports; firmware and Wi-Fi SSID in its attributes |
| `sensor.<device>_cloud_status` | `online` / `offline`; MAC in its attributes |
| `update.<device>_firmware` | installed version, and whether one is waiting |

### Controls

| Entity | Values |
|---|---|
| `number.<device>_screen_brightness` | 0–100 % |
| `select.<device>_screen_off_time` | 1, 5, 10, 30 minutes, or always on |
| `select.<device>_charging_mode` | adaptive power, thermal safe, DC turbo, priority |
| `switch.<device>_screensaver` | the clock the screen shows once it sleeps |
| `select.<device>_time_format` | 12- or 24-hour |
| `select.<device>_clock_style` | the two faces the charger draws |
| `select.<device>_wallpaper` | any picture in your UGREEN library, or none |

The choices match the app's own, and every write is read back from the device on
the next poll rather than assumed.

`custom` is a real charging mode and is reported when the device is in it, but it
cannot be selected here: it needs the 35 parameter bytes the presets leave at
zero, which only the app's mode editor fills in.

Ports are reported in the order `C1 C2 C3 C4 C5 C6 A1 DC`. A port keeps its
entities once it has been seen, so unplugging a cable does not delete its
history.

### Charging sessions

A session is a *bout of charging*: it starts when current begins to flow and its
total stays on screen afterwards, so *how much did that get?* is still answerable
once the phone is back in your pocket. The next bout starts a new session from zero.
Both session sensors carry the same detail in their attributes: `charging`,
`started`, `ended`, `duration`, `peak_power`, `average_power`, `last_draw` and
`protocol`.

It is a bout rather than the span between plugging in and unplugging because **this
charger cannot tell you a device has been removed.** It holds the port live and keeps
the negotiated USB-PD contract alive with nothing but a cable in the socket — 5 V and
`PD`, indistinguishable from an attached device that is not currently drawing. The
report's per-port occupancy byte says "present" for a bare cable too. So the end of a
session is decided by the current going away and staying away, which is what
*Session ends after* configures; a port that does report itself empty — some do —
ends its session at once instead of waiting that out.

That setting is a real trade-off, and the right value depends on what lives on the
port. A phone left at 100% tops itself up every so often, and those sips have to land
inside the same session, or a 5 mAh trickle would start a "new session" and the charge
that actually went into the phone would disappear off the card. The two-hour default
clears that comfortably. The cost is at the other end: two devices swapped on the same
cable less than two hours apart are counted as one session.

The charger reports no energy total, so this is integrated from the per-port
wattage, and three things about the device shape how:

- **Watt-hours are the measurement; milliamp-hours are a conversion.** A port may
  be handing over 5, 9 or 28 V, so the charge that reaches a battery depends on
  that battery's own voltage — which the charger cannot know. Set it under
  *Battery voltage* in the options; the 3.85 V default suits phones and earbuds
  and is meaningless for a laptop.
- **The cloud drops out for minutes at a time.** An outage leaves a session
  exactly as it was rather than reading as an unplug, and the missing minutes are
  not filled in with the last known wattage.
- **Neither current nor power can be trusted on its own.** A full device still
  reports the 0.1 A measurement quantum, which at 9 V looks like 0.9 W and would
  invent close to a whole battery over a night; a bare cable produces the mirror
  image, a stray 0.3 A that the charger itself reports as 0.0 W. Charge counts as
  flowing only when both are above their floors: 0.15 A and 0.5 W.

Sessions survive a restart of Home Assistant. If the device on the port changed
while it was down, or the downtime outlasted the idle window, the old total is
left as it was rather than added to.

**What the device does not offer.** Its TSL model declares `WiFiRSSI`,
`errorCode`, `IPAddress` and more, but this charger never populates them — asking
for those identifiers returns the same four properties it always reports. There
is no temperature sensor of any kind, and no energy total, so the charger cannot
feed Home Assistant's Energy dashboard directly (a Riemann-sum helper over
`total_power` is the usual workaround; the session sensors above measure a
device's stay on a port, not a running total).

## The screensaver card

The entities above are enough to automate with, but the screensaver is easier to
set the way the app sets it: one panel, with a live preview of the strip the
charger will actually show.

<img src="docs/screensaver-card.png" alt="The screensaver card" width="340">

The card is **served by the integration itself**, so there is nothing to add in
HACS and no resource to register — install the integration, restart, and the
card is available. Add it to a dashboard with *Add card → Manual*:

```yaml
type: custom:ugreen-plus-wallpaper-card
```

That is the whole configuration for a single charger. With more than one, name
the device:

```yaml
type: custom:ugreen-plus-wallpaper-card
device_id: 0123456789abcdef0123456789abcdef   # Settings → Devices → your charger, from the URL
title: Screensaver                            # optional; the card's heading
```

| Option | Default | Meaning |
|---|---|---|
| `device_id` | first charger found | which charger the card controls |
| `title` | `Screensaver` | heading next to the on/off switch |

Turning the switch off hides the settings, exactly as the app does — there is
nothing to configure while the screensaver is off.

**If the card does not appear** after installing, reload the browser with a hard
refresh (Ctrl/Cmd + Shift + R). The dashboard can render once before the
integration has finished starting, and shows *Configuration error* until the
page is loaded again.

### Your own wallpaper

*Upload a picture* opens a crop window over the photo you choose — drag, zoom
and rotate under it. The screen is **560 × 170**, wide enough that a photo almost
never suits it as taken, and zoom cannot go below the size that fills the window,
so a wallpaper never ends up with empty edges.

The charger keeps **one** slot of its own alongside the built-in pictures, so
uploading replaces whatever custom picture it was holding.

For automations there is a service taking a local `path`, a `url`, or base64 in
`image`:

```yaml
action: ugreen_connect_plus.set_wallpaper
data:
  device_id: 0123456789abcdef0123456789abcdef
  path: /config/www/desk.jpg
```

Pictures given to the service, rather than to the card, are cover-cropped from
the centre.

Uploading needs **Pillow**, which any Home Assistant with `default_config`
already has. Everything else in the integration works without it.

## How it works

Two clouds are involved.

**Account API** (`api2.ugreeniot.com` for Europe). Credentials are sent inside an
RSA envelope: `getSidInfo` hands out a short-lived `sid` plus an RSA-2048 public
key, the whole login body is encrypted with PKCS#1 v1.5 and posted as
`{data, sid}`. This yields the account access token and the device inventory.

**RTCX/Polaris gateway** (`eu-gateway.ugreeniot.com`) carries telemetry. It wants
its own token, obtained by trading a one-time OAuth code:

```
GET  /app/v1/variety/getAppInfo?platform=rtcx  -> appKey, appSecret, oauthClientId, authFlag
POST /app/v1/oauth/authorize                   -> data.code            (single use)
POST /client/account/third/login               -> data.accessToken     (the iotToken, 24 h)
```

The gateway login's `password` field carries **that OAuth code**, not the user's
password — `pwdType: "4"`, `accountType: "6"`. Gateway requests are signed
Alibaba-API-Gateway style: `HMAC-SHA256(appSecret, stringToSign)` in
`x-ca-signature`, over `x-ca-key`, `x-ca-nonce` and `x-ca-timestamp`.

`appKey`/`appSecret` are **fetched at runtime under your own account** and are
not embedded in this repository.

### The charger's binary protocol

Readings are not exposed as named properties. The device tunnels a small binary
protocol through a single property, `PT_data`:

```
TYPE(1) CMD(1) LEN(2, big endian) PAYLOAD(LEN) CRC16(2)
TYPE: 0xAA query   0xEE device notify   0x11 setting
CRC:  CRC-16/MODBUS, low byte first
```

Writing a query frame to `PT_data` (`thing/properties/set`) makes the device
answer; the reply becomes the property's value, read back with
`thing/properties/get/all`. `GET_POWER_INFO` (`0xAA 0x06`) answers with eight
7-byte port records:

| offset | field | encoding |
|---|---|---|
| `7*i + 0` | voltage | U16 big endian, tenths of a volt |
| `7*i + 2` | current | U16 big endian, tenths of an amp |
| `7*i + 4` | power | U16 big endian, tenths of a watt |
| `56 + i` | handshake protocol | U8 |

Since the property retains its last value indefinitely, readings older than five
minutes are treated as "no reading" rather than as live. A reply is waited for by
polling until the frame is the one that was asked for, because until then the
property still holds the previous command's answer.

Wallpapers are the one thing not sent as bytes. The picture is uploaded to
UGREEN's own storage (`upload-pre-info` → presigned `PUT` → `wallPaper/save`) and
the charger is then handed the resulting id and URL through a `PIC_data`
property, which is what makes it download the file.

## Blueprint

An automation blueprint ships with the integration, for the thing people
actually want from a charger: being told when something has finished.

[![Import the blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Flukislp%2Fugreen_connect_plus%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fugreen_connect_plus%2Fcharging_finished.yaml)

Pick a port's charging event entity and how you want to be told — the default
posts a notification in Home Assistant itself, and the picker offers everything
else. The message carries how much went in, how long it took, the peak and the
protocol; it is also available as `{{ message }}` if you would rather write your
own.

It exists because the interesting part is not the notification but the
decision. A full phone still reports the charger's 0.1 A measurement quantum,
and a bare cable produces a stray current the charger itself rounds to 0 W, so
"has it finished" written as a template threshold either announces a laptop
that is still charging or misses one that finished an hour ago. The integration
has to settle that question for its own session tracking, and the event entity
publishes the answer — the blueprint just listens for it.

A port that pauses and draws again is one session rather than several, so this
fires once at the end rather than at every dip. There is a floor on the energy,
because plugging a cable in and straight out again is a real session and not
worth a notification.

## Settings

*Settings → Devices & Services → UGREEN Connect Plus → the cog on the account row*:

<img src="docs/options.png" alt="The options dialog" width="480">

| Setting | Default | Notes |
|---|---|---|
| Poll every | 5 s | how often a reading arrives, measured start to start; the wait for the charger to answer comes out of it, not on top |
| Battery voltage | 3.85 V | only used to read a session's watt-hours back as milliamp-hours; a laptop's pack is far higher |
| Charging efficiency | 90 % | how much of what leaves the port reaches the cell; the rest is heat |
| Session ends after | 120 min | how long a port must deliver nothing before its charging session is finished; see [Charging sessions](#charging-sessions) for the trade-off |
| Region | as set up | only if the account itself moved servers; the password is re-checked first |
| A device for each port | on | every port is its own device; off puts everything on the charger. Switching moves entities between devices, keeping their history but not what points at the device |
| Debug snapshot | off | writes the unedited cloud payload to `ugreen_connect_plus_debug.json` |

Five seconds keeps the wattage live enough to watch a laptop charge. It is also
a lot of traffic against someone else's API, so raise it if you would rather be
gentle — nothing else depends on the rate.

## Tests

The session rules -- what starts one, what ends it, what a dropout means -- are the
one part of this with enough edge cases to be worth pinning down, so they live in
`session.py` with no Home Assistant imports and are tested on their own:

```
pip install pytest && pytest tests -q
```

Everything else needs a real charger and a real cloud account to say anything, and
is checked against both rather than mocked.

## Translating

Everything the integration says goes through
`custom_components/ugreen_connect_plus/translations/`. Copy `en.json`, name it for
your language, translate the values, and open a pull request. English, German
and Russian exist so far.

The dashboard card keeps its own text in one table at the top of
`www/ugreen-plus-wallpaper-card.js`: copy the `en` block, key it by language code,
and translate. Missing keys fall back to English, so a partial translation is
fine.

## Things worth automating

**Say what a charge came to.** The event carries the figures, so nothing has
to be read back:

```yaml
triggers:
  - trigger: state
    entity_id: event.ugreen_nexode_pro_x783_c3_charging
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type == 'ended' }}"
actions:
  - action: notify.persistent_notification
    data:
      message: >-
        C3 delivered {{ trigger.to_state.attributes.energy_wh }} Wh in
        {{ (trigger.to_state.attributes.duration / 60) | round }} minutes
        over {{ trigger.to_state.attributes.protocol }}.
```

**Put it on the Energy dashboard.** *Settings → Dashboards → Energy →
Individual devices*, and add either the charger's own total or the per-port
counters -- one or the other, since together they count every watt-hour
twice.

**Notice when the readings stop being current.** The cloud goes quiet for
minutes at a time; this says when it has been longer than that:

```yaml
triggers:
  - trigger: template
    value_template: >-
      {{ (now() - states('sensor.ugreen_nexode_pro_x783_last_successful_poll')
          | as_datetime).total_seconds() > 900 }}
```

**Something that reacts to charging itself** takes the flag rather than a
wattage, which on this charger cannot tell a full battery from a bare cable:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.ugreen_nexode_pro_x783_c3_charging
    to: "off"
    for: "00:05:00"
```

## Limitations

- **Cloud polling only**, five seconds apart by default — see *Settings* above.
- Logging in from the app with the same account can invalidate the integration's
  token. It re-authenticates on rejection, so this is self-healing.
- Per-port switching (`SET_PORT_CONTROL`) and the `custom` charging-mode editor
  are decoded but not exposed. `FACTORY_RESET` is deliberately left out.
- Settings changed from the phone app show up here on the next poll, wallpapers
  included: a picture uploaded there is named and previewed within a minute,
  because an id the library cannot account for sends the integration to read it
  again. The reverse is not true — **the app caches**, and keeps showing its old
  value until it is force-stopped and reopened.

## One instance per account

The charger answers into a single cloud property, and the gateway appears to
keep one session per account. Two Home Assistants signed in to the same UGREEN
account therefore do not merely share a charger — they unseat each other. Each
login invalidates the other's token, whose next request is rejected, which
makes it log in again; the cloud eventually answers `Too many requests` (code
770003) and stops talking to both.

Watched, not deduced: a second instance made every entity of the charger blink
out and back every few seconds, and the account was rate limited within two
minutes. This release backs off rather than feeding the loop, but the way out
is to run one instance against an account. A second household member's phone
app is fine; a second Home Assistant is not.

## Contributing

Issues and pull requests are welcome, especially from owners of other UGREEN
chargers — the port table and the byte offsets in `const.py` and `rtcx.py` are
specific to the 300W eight-port model and are the first thing another one will
disagree about. A
diagnostics download is the most useful thing to attach, because it carries the
raw frames themselves. Take it from the charger's own page (*Settings → Devices
& Services → UGREEN Connect Plus → the charger → Download diagnostics*) rather
than from the account row, and it answers about that charger alone — with two
of them on one account, nobody has to hand over the other or work out which
half is relevant. It is written to be safe to post in public: the
account, the charger's serial number and MAC, its cloud id and the Wi-Fi
network name it is joined to are all left out of it.

## Legal

Written for interoperability, using the exception in Directive 2009/24/EC Art. 6
(UK: CDPA s.50B). No UGREEN code is redistributed, and no credentials of theirs
are embedded. MIT licensed.
