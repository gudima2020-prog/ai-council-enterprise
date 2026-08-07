# OCR runtime third-party components

The isolated OCR image contains the following major components:

- Tesseract OCR: Apache License 2.0.
- pypdfium2 5.8.0: Apache-2.0 or BSD-3-Clause.
- PDFium binary distributed by pypdfium2: BSD-style license.
- Pillow 12.3.0: HPND license.
- Python 3.13 slim Bookworm base image and Debian packages under their
  respective licenses.

The application does not import pypdfium2 or Pillow on the host. They are
contained inside the separately built, trusted OCR runtime image. Runtime
execution uses the inspected immutable image ID, disables network access,
uses a read-only root filesystem, drops Linux capabilities, enables
no-new-privileges and stores rendered page images only in container tmpfs.
