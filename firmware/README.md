
## Build

### Fetch Dependencies

```bash
python3 ./fetch_repos.py
```

### Tool Chains

[ESP-IDF v6.1](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32s3/index.html)

### Build

```bash
idf.py build
```

### Host-side tests

Motion, audio cancellation leases, head-pet timing, BLE queue bounds and the
actual SD manifest parser can be tested without ESP-IDF or hardware. If you
already fetched firmware dependencies, CMake finds ArduinoJson automatically.
For a clean checkout, fetch only its pinned header library:

```bash
python3 tests/fetch_host_dependency.py /tmp/stackchan-host-arduinojson
cmake -S tests -B build-host-tests -DARDUINOJSON_INCLUDE_DIR=/tmp/stackchan-host-arduinojson/src
cmake --build build-host-tests
ctest --test-dir build-host-tests --output-on-failure
```

### Flash

```bash
idf.py flash
```

## Agent image and maintenance

This variant starts AI Agent directly, even when an older NVS setting disabled
auto-start. Legacy launcher indices are cleared. Standalone Dance, Avatar UI,
ESP-NOW remote, EZData, AppCenter and the desktop launcher are reference source
only: their apps/services are excluded from the image. Agent expressions,
servos, touch, BLE, RGB, reminders and volume remain available.

Swipe the bottom home indicator to reboot into Maintenance. It contains Wi-Fi
provisioning, network diagnostics, servo calibration, brightness, volume,
power/idle motion preferences, version information and an explicitly selected
manual update. Swipe home there to reboot into Agent. A missing network also
opens Maintenance. Agent startup never checks or downloads firmware/assets.
For recovery, keep a known-good full flash backup and use USB flashing; never
erase NVS/OTA/assets just to update the application partition. A manual update
can replace customization, so deploy a reviewed project build when retaining
these features matters.

For an unattended deployment, `idf.py -DDANIA_BOOT_CHIME=OFF build` disables the
ready chime without changing saved volume. Its public default is ON. This does
not mute alerts, replies or touch recordings.

## SD voice pack

Use FAT32. The firmware never formats a card. Files belong under
`/sdcard/dania/voice-pack/`; a missing card or invalid pack leaves embedded
head-pet audio (when privately supplied) and cloud speech available. Public
source includes no private recordings. Keep the card inserted during playback;
replace packs while powered off, then reboot to reload the manifest.

The SD and display share SPI3 on this board: MOSI 37, clock 36, SD MISO/LCD D-C
35, SD CS 4 and LCD CS 3. ESP-IDF 6.1's LCD SPI pre/post callbacks release D-C
after **every** transaction, including command/parameter polling, allowing SD
reads between display transactions. Do not replace this with a second SPI bus
initialization or a driver that leaves GPIO 35 driving continuously. AW9523
P0-4 powers the card; the firmware preserves other expander outputs.

`manifest.json` uses this version-one shape:

```json
{
  "version": 1,
  "pack_id": "my-pack-v1",
  "clips": [
    {
      "id": "01_hello",
      "file": "01_hello.ogg",
      "bytes": 12345,
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "crc32": 123456789
    }
  ],
  "headpet": ["01_hello"]
}
```

Use the real file byte count, SHA-256 and unsigned IEEE CRC-32, not these example
values. IDs and pack IDs contain only ASCII letters, digits, hyphen and
underscore, up to 64 characters; files are safe basenames ending `.ogg`.
The manifest is at most 32 KiB with 1–64 clips, each at most 256 KiB. Head-pet
references contain up to ten distinct existing IDs. Duplicates, traversal,
missing files, size/CRC mismatches and malformed metadata reject the pack.
SHA-256 is retained as metadata; actual integrity checking uses CRC-32.

Every SD playback first verifies the complete file's CRC, mono Opus/Ogg
structure and duration (at most fifteen seconds), then streams 1 KiB chunks on
one low-priority worker into the bounded audio queue. It never allocates a
card-sized or whole-clip PCM buffer. An audio-generation lease rejects old
packets after cancellation/listening/network reset and waits for actual output
drain before reporting completion. No automatic full replay follows a failure
after audio has been accepted, avoiding duplicate partial replies. Head-pet
playback requires idle, has a six-second cooldown and avoids immediate repeats
when the pack has more than one head-pet clip.

`getStorageInfo` on the BLE config channel (with a uint32 `request_id`) returns
cached `cmd: "storage"`, `mounted`, `valid`, `pack_id`, `clips`, `total_mb`,
`free_mb` and `error`. Capacity is counted once on the SD worker; it can be zero
briefly during startup. No FAT scan runs in BLE/UI/audio callbacks.

For protocol integration, see `main/hal/board/sd_voice.h`. A successful
`request_sd_voice` return means queued only. Callbacks run on the worker:
`Accepted` follows validation and reservation; `Completed` follows audio drain;
`Failed` and `Cancelled` are terminal. Callbacks must schedule application and
network work onto their owner thread. Finish entering Speaking before queuing a
cloud local clip, because that state transition resets the audio generation.

### Optional private first-boot seed

An external C++ file can implement `dania_sd_bootstrap_files` from
`main/hal/board/sd_voice_seed.h` and supply static byte arrays for the clip
basenames plus `manifest.json`. Build with
`idf.py -DDANIA_SD_SEED_SOURCE=/absolute/private/seed.cc build`.
The public weak hook supplies nothing. **Never publish the private source or
the resulting binary.**

The storage code validates the complete seed before writing, writes only the
owned directory, compares rather than replaces existing files, and commits the
manifest last via a temporary file and rename. Existing manifests (even corrupt
ones), unknown conflicting files and interrupted `.seed-tmp` files are left
alone for manual recovery. No SD failure erases settings or formats the card.
