# License the project under GPL-3.0-or-later

The repo is public, and until now it had no license, so legally nobody could reuse any of it. We license it GPL-3.0-or-later instead of a permissive licence like MIT or Apache-2.0. The rink renderer `hockey-rink` is GPL-3.0 (ADR-0009), and so is the bundled ffmpeg build via `libx264` (ADR-0010). Any distributed copy of the app, and especially a future packaged executable, already carries GPL obligations. A permissive licence on our own source would suggest a freedom that the distributed app can't actually give. "Or later" follows the usual GPL default and stays compatible with `hockey-rink`'s GPL-3.0.

This is hard to reverse once anyone else contributes, because relicensing would need their agreement. Going permissive later would also mean replacing both GPL dependencies first.
