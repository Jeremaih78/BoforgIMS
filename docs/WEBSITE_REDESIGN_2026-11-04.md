# Boforg homepage redesign — 4 November 2026

The homepage gives visitors direct routes to printing packages, equipment, supplies and home fitness. The setup finder helps first-time buyers explain their printing goal, budget and experience before opening a WhatsApp enquiry.

## Research and decisions

- [Baymard's homepage and navigation research](https://baymard.com/research-articles/ecommerce-navigation-best-practice) informed visible, recognisable categories, clear paths into the catalogue and prominent search.
- [Baymard's mobile homepage research](https://baymard.com/research-articles/mobile-homepage-usability) informed keeping the breadth of Boforg's catalogue visible on phones, rather than hiding the entire offer inside a menu.
- [Cricut's equipment guidance](https://cricut.com/en-us/discover-heat-presses) reinforces matching equipment and accessories to the intended project. The guide asks about the intended product rather than promising one machine can print every fabric or material.
- The previous page relied on a long capitalised heading, a placeholder banner, repetitive cards and unsupported earnings examples. These were replaced with concise copy, actual product imagery, current catalogue prices and stock-aware purchase actions. No testimonials, sales volumes or earnings claims were invented.

## Experience

- Blue brand colour with warm neutral backgrounds, a clearer type hierarchy, visual categories, real products and a simpler header/footer.
- Search, product details, cart and package purchase routes preserve their existing shop endpoints, discounts and stock checks.
- The three-question guide updates immediately with JavaScript and also renders from GET parameters without JavaScript. Only validated choices appear in the enquiry. No contact details are collected or stored, and nothing is sent until the visitor follows the WhatsApp link and sends their message.
- Native details/summary elements support package contents and FAQs. Mobile navigation supports Escape and closes after selecting a link. Touch controls, visible focus styles, reduced-motion support and a mobile contact bar are included.
- Current catalogue data is read through the existing public-product and public-package boundaries. Private, inactive and zero-price products are excluded; reserved inventory is reflected in purchase availability. Empty catalogues have a useful fallback.
- The homepage uses no framework, font service, new dependency or database migration. Images below the hero are lazy loaded. Catalogue reads are bounded and prefetched. The page prevents caching of personalised cart counts and availability.
- Existing optional analytics records category clicks, setup-guide starts and WhatsApp CTA clicks. This is a conversion-oriented redesign, not a measured conversion uplift; evaluate actual enquiries after release.

## Validation and release

50 focused Django tests cover new homepage behaviour, public catalogue rules, live discounted package prices, stock reservations, hidden components, no-JavaScript guide parameters, encoded enquiries, safe invalid inputs, unchanged business data on GET, shared shop templates and host routing. JavaScript syntax passed.

Browser checks cover 320, 390, 768 and 1440 pixel widths, mobile navigation, guide selections, package expansion, adding a package to the local cart, image loading and browser errors. Server checks and the deployment receipt are recorded separately in the private release directory and local output report.

Unrelated staff-email work was left in the original checkout. This release was developed in its own worktree and includes only homepage code, templates, styles, tests and documentation.
