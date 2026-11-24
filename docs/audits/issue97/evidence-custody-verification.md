# Issue #97 — restricted evidence custody verification

The 20 raw artifacts introduced by `ff15058` were preserved outside the Git
repository in restricted user-controlled storage at `~/.hermes/private/issue97-canary-ff15058/`
(directory mode 0700, files 0600). No restricted payload, credential or signed URL
is reproduced in this repository. Hashes and byte counts below are safe references.

| Preserved path | SHA-256 | Bytes | Recovery verified |
|---|---|---:|---|
| `runs/jw-canary-2026-11-24/batch-01.json` | `4317ad46e0cce62e92926807933281e6a0b7429ad0c522513fa48a4fe7ee0602` | 1132 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/batch-02.json` | `8406aa59c9e772e297ad9047b5a5cbcb086c3b86c1a108375199483d1777173f` | 381 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/batch-03.json` | `5a2cd834d559d588bb883c6b8126083fae4381bf37d4efcbb881c4ddcf3e6e18` | 423 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/call-01-inception.json` | `5756a1959fb0eeaadde760ba2e0371d444d8c03544459adb229d5900f5a3ccac` | 1611 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/call-02-dune.json` | `47922ee5abc249324f4e2cf3b319d8aed8528d5464c1d599047ec711fb16448e` | 48246 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/call-03-inception-retry.json` | `79e90f57a41eea5ec0452d2772f034cca50358ed1c93eb57e051c81a8c0ab0fe` | 47393 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/coverage.json` | `96d514638731f8b328bc269af4c6a3cb5d887adb9f1bdc09b03d929f8feebf35` | 736 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/function_show_redeploy.json` | `e25f372188c7c1f9c17c898af1b930ff482b7addae8e9a82c690e8036a9a219c` | 5604 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/manifest.json` | `f5d28b45a45b55ecdad1eb0895f4231b4bd0adffdcf9af7d38db39a02552f3b2` | 2223 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/offers.csv` | `caabaf16fe2c439c005b23d7508b09882339964db4c92c4d5d81c018348a8578` | 68988 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/offers.json` | `e9f1bd0f7fe2e2cb5564662ad11c4c1f54ade94f1780d089a6fe49434e828220` | 95952 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/probe-01.json` | `6bd38c95f2b25d374c0784f472b2711a4745b8e31e04bfe7ba2cd5603e74c577` | 1458 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/rejects.json` | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` | 2 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/summary.md` | `c174f48a348d07a0b5871931b20dd32f35f8d39c0e007279f2fffcfe98aa89eb` | 1529 | byte-identical to Git blob |
| `runs/jw-canary-2026-11-24/validation_log.json` | `f1e0983c1ae1ffe0ad6c6851bc17c86b06278fd9e35632be13993dd0c2539de6` | 7634 | byte-identical to Git blob |
| `spec/PENDING.md` | `318399c7e81ebf34016dcdef9df294a1f227af7a98543d03efd01e524393ca79` | 2878 | byte-identical to Git blob |
| `spec/fe1b97b6.py` | `b512a7becc416a8609c251396184cb7330c03a22a72b2235fa3831078bbc20a5` | 22453 | byte-identical to Git blob |
| `spec/fe1b97b6.yaml` | `faf8b4ba69835f0428821b71a0c29f6072fe0aab0647016b8148120005e0a23f` | 4665 | byte-identical to Git blob |
| `spec/fe1b97b6_v2.py` | `b512a7becc416a8609c251396184cb7330c03a22a72b2235fa3831078bbc20a5` | 22453 | byte-identical to Git blob |
| `spec/manifest.json` | `124b50d9c46c32f739bf7484cf62b63819ebf30282f90ce4e246ed13f3f8aa36` | 956 | byte-identical to Git blob |

- Source commit: `ff150586319e89b8ce4f8dfde9403a33eceea1d1`
- Preserved files: 20; total bytes: 336717
- Verification: SHA-256 match 20/20, byte-count match 20/20, byte-for-byte comparison to `git show` blob 20/20.
- Custody: files remain in restricted storage; this cleanup does not delete them.
- No history rewrite, force push, credential rotation, provider call or runtime change was performed.
