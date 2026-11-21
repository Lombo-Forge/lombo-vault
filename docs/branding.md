# The Lombo look

## Contents

- [The rule](#the-rule)
- [This app's mark](#this-apps-mark)
- [Colours and type](#colours-and-type)
- [The seal and the L](#the-seal-and-the-l)
- [What every app needs](#what-every-app-needs)
- [What to avoid](#what-to-avoid)

## The rule

**The Lombo L is in every icon.** Each app has its own seal, and a small mark of its own sits in
the open corner of the L. Each app also gets one wordmark trick (Tier's letters step down,
Shrink's get smaller from S to K, Shelf's F leans).

## This app's mark

LomboVault's mark is a **small vault door**: a double ring (the ember orange outer ring, a thin muted inner ring) around a three-spoke hand wheel with a
round hub, upright (not tilted), in the L's open corner: right of the stem, above the foot, clear of both. The wordmark trick: a **bolt bar** slides
under "VAULT" and ends in a round catch. The source is `assets/icon.svg` and `assets/banner.svg`; the PNGs are rendered from them with the Cinzel font
installed (see the `forge-branding` skill in the workspace).

## Colours and type

| Use | Value |
|---|---|
| Background | `#0d0a08` |
| Surface | `#1a140f` |
| Borders | `#2c231a`, `#443528` |
| Text, muted | `#f4ede3`, `#b3a696` |
| Ember gradient | `#ffb347` to `#ff6a1a` to `#e23d0f` |

Headings and wordmarks: Cinzel (SIL Open Font License). Body text: Geist.

## The seal and the L

`assets/icon.svg` is the base seal: a ring with the gradient, an inner frame line, and the L.
Copy it, keep the ring and the L, and add the app's mark to the open corner (right of the stem,
above the foot). Banners use the same seal on the left and the wordmark on the right.

## What every app needs

- `assets/icon.svg`, plus a 512 px PNG for the container registry and GitHub.
- `assets/banner.svg`, shown at the top of the README (1200 x 300).
- A 1280 x 640 social preview image, uploaded in the repository settings.

SVG text uses fonts the viewer may not have, so export the PNG versions with the font installed
and use those where exact lettering matters.

## What to avoid

- No Tolkien names, scripts, rings or symbols. The feel comes from engraved capitals, a map-style
  frame and the forge colours, not from borrowed imagery.
- No logos, names or colours of other products. "For Jellyfin" is fine in a description; never
  imply the Jellyfin project made the app.
