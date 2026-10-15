<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/dark_logo.png">
    <img src="img/logo.png" alt="Hoymiles Solar Cloud" width="460">
  </picture>
</p>

# Hoymiles Solar Cloud Integration for Home Assistant

A modern, native Home Assistant custom integration (HACS) to monitor your **Hoymiles Solar System** using your existing Hoymiles hardware and DTU (DTU-W100, DTU-Pro, DTU-WLite, etc.) via the S-Miles Cloud API.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![GitHub release](https://img.shields.io/github/v/release/ValtteriAho/hassio_hoymiles?include_prereleases)](https://github.com/ValtteriAho/hassio_hoymiles/releases)
[![License](https://img.shields.io/github/license/ValtteriAho/hassio_hoymiles)](LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-yellow.svg?logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/valtteriah0)
[![Donate PayPal](https://img.shields.io/badge/Donate-PayPal-00457C.svg?logo=paypal&logoColor=white)](https://paypal.me/valtteriah0)

---

## ✨ Features

- **No Extra Hardware Needed**: Works directly with your existing Hoymiles inverter and DTU.
- **Zero MQTT Overhead**: Native Home Assistant integration. No Mosquitto broker or Docker add-on needed.
- **Energy Dashboard Ready**: Sensors come pre-configured with `device_class: energy` and `state_class: total_increasing` for instant use in Home Assistant's Energy panel.
- **Easy UI Setup**: Add and authenticate directly through the Home Assistant UI (*Settings > Devices & Services*).
- **Auto-Discovery**: Automatically discovers your solar plant(s), capacity, and micro-inverters.
- **Modern Authentication**: Supports the latest Hoymiles Argon2 authentication challenge flow and V3 cloud API endpoints with automatic session renewal.

---

## 📊 Entities Provided

### Solar Plant
- **Current Power** (`W`)
- **Today's Energy** (`kWh`) — *Energy Dashboard compatible*
- **Month's Energy** (`kWh`)
- **Lifetime Energy** (`kWh`) — *Energy Dashboard compatible*
- **CO₂ Saved** (`kg`)
- **Trees Saved** (`trees`)
- **Generating Status** (`binary_sensor`)

### Micro-Inverters
- **Cloud Connectivity** (`binary_sensor`)
- **Problem / Alarm Status** (`binary_sensor`)
- **Alarm Code** (`diagnostic sensor`)
- **Alarm Message** (`diagnostic sensor`)

---

## 🚀 Installation via HACS (Recommended)

1. Open **HACS** in your Home Assistant sidebar.
2. Go to **Integrations**, click the three dots (**⋮**) in the top right corner, and select **Custom repositories**.
3. Add:
   - **Repository:** `https://github.com/ValtteriAho/hassio_hoymiles`
   - **Category:** `Integration`
4. Click **Add**, find **Hoymiles Solar Cloud**, and click **Download**.
5. Restart Home Assistant.
6. In Home Assistant, go to **Settings** > **Devices & Services** > **Add Integration**, search for **Hoymiles Solar Cloud**, and follow the on-screen instructions.

---

## ⚙️ Configuration Options

Once installed, you can click **Configure** on the integration card to adjust the **Update Interval** (default: `480` seconds / 8 minutes to respect Hoymiles cloud rate limits).

---

## 📦 Legacy Add-on (Archived)

The previous Home Assistant Add-on implementations (Edge, Stable, and OldStable) that bridged Hoymiles cloud data via MQTT have been archived in the [`legacy/`](legacy/) folder for backward compatibility:

- [`legacy/edge/`](legacy/edge/) — Refactored Python add-on with MQTT publisher.
- [`legacy/stable/`](legacy/stable/) — Stable version 1.4.x add-on.
- [`legacy/oldStable/`](legacy/oldStable/) — Original add-on version.

---

## ☕ Support Ongoing Development

This integration is completely free and open source. Maintaining cloud integrations requires ongoing effort: reverse-engineering manufacturer cloud API changes (such as Hoymiles' Argon2 security updates), testing compatibility across micro-inverter and DTU models, and adapting to monthly Home Assistant Core updates.

If this integration helps you monitor your solar generation and brings value to your smart home, consider supporting its continued development:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-yellow?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/valtteriah0)
[![Donate with PayPal](https://img.shields.io/badge/Donate-PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/valtteriah0)

*Every coffee or donation is deeply appreciated and directly helps support continued maintenance and improvements!*

---

## 🙏 Credits & Acknowledgments

This project originated from and was inspired by the work on [**dmslabsbr/hoymiles**](https://github.com/dmslabsbr/hoymiles) by [@dmslabsbr](https://github.com/dmslabsbr) and community contributors. 

While this repository reimagines and evolves the solution from a standalone MQTT add-on into a native Home Assistant custom integration, grateful credit and appreciation go to the original authors for reverse-engineering the Hoymiles cloud API protocols and laying the foundation.

