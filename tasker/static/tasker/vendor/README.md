# Tasker pinned frontend assets

These copies remove a runtime CDN dependency for Tasker pages. IMS pages retain their existing asset configuration. No package installation or package scripts were run. Only the named CSS, JavaScript, source maps, fonts and license files listed below were copied from each archive. Bootstrap source maps are included because the pinned CSS/JavaScript reference them and production manifest storage validates those references during static collection.

## Sources and verification

### bootstrap 5.3.3

- Registry metadata: https://registry.npmjs.org/bootstrap/5.3.3
- Official npm registry tarball: https://registry.npmjs.org/bootstrap/-/bootstrap-5.3.3.tgz
- License: MIT; full text is retained at `bootstrap/LICENSE`.
- Registry SHA-512 integrity verified before copying: `sha512-8HLCdWgyoMguSO9o+aH+iuZ+aht+mzW0u3HIMzVu7Srrpv7EBBxTnrFlSCskwdY1+EOFQSm7uMJhNQHkdPcmjg==`
- Tarball size: 1684364 bytes.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `bootstrap/css/bootstrap.min.css` | 232803 | `3c8f27e6009ccfd710a905e6dcf12d0ee3c6f2ac7da05b0572d3e0d12e736fc8` |
| `bootstrap/js/bootstrap.bundle.min.js` | 80721 | `0833b2e9c3a26c258476c46266e6877fc75218625162e0460be9a3a098a61c6c` |
| `bootstrap/js/bootstrap.bundle.min.js.map` | 332090 | `5e3e0763164143baaa1ca0706b6100ba0452f911d6ce9713b48e3dbe07b35125` |
| `bootstrap/css/bootstrap.min.css.map` | 589892 | `f12338536350a422c64d02d6e43ff1dea493c3156ad823fe19761cdd5d56c05b` |
| `bootstrap/LICENSE` | 1093 | `8c14611ae41ac6fd543c13349f22188eb12c69b3e59105c5eca3925a8e4eca3e` |

### bootstrap-icons 1.11.3

- Registry metadata: https://registry.npmjs.org/bootstrap-icons/1.11.3
- Official npm registry tarball: https://registry.npmjs.org/bootstrap-icons/-/bootstrap-icons-1.11.3.tgz
- License: MIT; full text is retained at `bootstrap-icons/LICENSE`.
- Registry SHA-512 integrity verified before copying: `sha512-+3lpHrCw/it2/7lBL15VR0HEumaBss0+f/Lb6ZvHISn1mlK83jjFpooTLsMWbIjJMDjDjOExMsTxnXSIT4k4ww==`
- Tarball size: 846254 bytes.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `bootstrap-icons/font/bootstrap-icons.css` | 98255 | `4ffa6bea4304d2eda418683f56261685ed47bf00995039f27e5ad62d53938d2d` |
| `bootstrap-icons/font/fonts/bootstrap-icons.woff` | 176032 | `bb1de989b83970f6f4e54de1cd974c5cba55b73582da5e1b225a6d0edf029483` |
| `bootstrap-icons/font/fonts/bootstrap-icons.woff2` | 130396 | `476adf42b40325098fcfa8b36ab3e769186bb4f6ce6a249753e2e1a9c22bf99e` |
| `bootstrap-icons/LICENSE` | 1093 | `0fb3e11bd57e896c5a512afd64864d28a37de45d19835016c87ca1ad19ead969` |

Machine-readable archive integrity and individual file hashes are in `integrity.json`. Keep the selected package versions unchanged unless a separately reviewed dependency update is requested. Bootstrap Icons CSS preserves its relative `fonts/` URLs.
