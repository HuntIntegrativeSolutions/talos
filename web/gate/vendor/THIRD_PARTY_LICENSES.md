# Third-party licenses — web/gate/vendor/

Both libraries are vendored as prebuilt, single-file browser bundles — no CDN fetch, no
build step. Neither is modified from upstream.

## marked.min.js

- Source: `marked` npm package v9.1.6, `lib/marked.umd.js` (the UMD browser bundle).
- License: MIT.
- Copyright (c) 2018+, MarkedJS (https://github.com/markedjs/); Copyright (c) 2011-2018,
  Christopher Jeffrey (https://github.com/chjj/).
- Upstream: https://github.com/markedjs/marked

## purify.min.js (DOMPurify)

- Source: `dompurify` npm package v3.1.7, `dist/purify.min.js`.
- License: dual Apache-2.0 / Mozilla Public License 2.0 (not MIT/BSD — accepted for this
  repo per explicit sign-off; DOMPurify is the industry-standard HTML sanitizer, actively
  maintained by Cure53, and is the mandatory pairing for any Markdown-to-`innerHTML`
  pipeline to avoid XSS. Both licenses are OSI-approved permissive licenses).
- Copyright 2024 Dr.-Ing. Mario Heiderich, Cure53.
- Upstream: https://github.com/cure53/DOMPurify

Full upstream license texts are reproduced below.

---

## MIT License (marked)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

---

## DOMPurify license

DOMPurify is free software; you can redistribute it and/or modify it under the
terms of either:

a) the Apache License Version 2.0, or
b) the Mozilla Public License Version 2.0

DOMPurify is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE. See the Apache License Version 2.0 and Mozilla Public
License Version 2.0 for more details.

Full text: https://github.com/cure53/DOMPurify/blob/main/LICENSE
