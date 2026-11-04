"""Public, read-only merchandising and a progressively enhanced setup guide."""
from urllib.parse import urlencode, urljoin

from django.conf import settings
from django.urls import reverse
from shop.context_processors import public_url, whatsapp_url
from shop.packages import public_packages
from shop.selectors import public_products

GOALS = {
    'shirts': {'label': 'T-shirts & clothing', 'title': 'Start with the right fabric and finish.', 'description': 'Compare heat transfer vinyl, DTF and sublimation. Tell us which fabrics you want to print so we can help you choose compatible equipment.', 'query': 'heat'},
    'gifts': {'label': 'Mugs & personalised gifts', 'title': 'Make everyday items personal.', 'description': 'Explore sublimation printers, presses and compatible blanks. Ask us which attachments and supplies your first products will need.', 'query': 'sublimation'},
    'vinyl': {'label': 'Vinyl & signage', 'title': 'Turn your designs into clean-cut results.', 'description': 'Explore vinyl cutters, rolls and finishing supplies. Share your planned materials and cutting size with our team.', 'query': 'vinyl'},
    'explore': {'label': 'I am still exploring', 'title': 'A good business starts with a good fit.', 'description': 'You do not need to know every machine yet. Tell us your budget and ideas, and we will help you compare a practical first setup.', 'query': ''},
}
BUDGETS = {'unsure': 'Help me work it out', 'under500': 'Under USD 500', '500to800': 'USD 500–800', '800to1500': 'USD 800–1,500', '1500plus': 'USD 1,500+'}
EXPERIENCE = {'new': 'Starting my first business', 'upgrade': 'Upgrading my current setup'}


def homepage_context(request):
    shop = public_url(request, 'shop:catalog')
    phone = ''.join(c for c in settings.BOFORG_WHATSAPP_NUMBER if c.isdigit())
    products = list(public_products().filter(slug__isnull=False).exclude(slug='').order_by('name')[:120])
    pictured = [p for p in products if p.image]

    def pick(terms):
        candidates = [p for p in pictured if any(term in f'{p.name} {p.category.name if p.category else ""}'.lower() for term in terms)]
        return next((p for p in candidates if p.shop_purchasable), next(iter(candidates), None))

    hero = next((p for p in pictured if '5in1' in p.name.lower() and p.shop_purchasable), pick(('heatpress', 'heat press')))
    specs = [('Heat presses', 'For garments, mugs & more', 'heat', ('heatpress', 'heat press')), ('Sublimation', 'Printers, inks & essentials', 'sublimation', ('sublimation printer', 'sublimation ink')), ('DTF printing', 'Film, powder & supplies', 'DTF', ('dtf',)), ('Vinyl cutting', 'Cutters, vinyl & tools', 'vinyl', ('vinyl cutter',)), ('Blanks & gifts', 'Your next personalised product', 'mug', ('mug', 'bottle')), ('Home fitness', 'A little more everyday movement', None, ('dumbbell', 'dumbell', 'yoga'))]
    categories = [{'name': title, 'description': description, 'url': shop + '?' + urlencode({'q': query} if query else {'department': 'fitness'}), 'product': pick(terms)} for title, description, query, terms in specs]
    featured = []
    for card in categories[:4]:
        product = card['product']
        if product and product.shop_purchasable and product not in featured:
            featured.append(product)
    for product in pictured:
        if len(featured) >= 4:
            break
        if product.shop_purchasable and product not in featured and product.category and product.category.department == 'printing':
            featured.append(product)
    for product in products:
        product.home_url = public_url(request, 'shop:product_detail', kwargs={'slug': product.slug})
    packages = public_packages(limit=12)
    for package in packages:
        package.home_price = package.compute_price()
        components = list(package.items.all())
        package.home_image_product = next((item.product for item in components if item.product.image), None)
        package.home_component_count = len(components)
    packages.sort(key=lambda package: (not bool(package.shop_available), package.home_price))
    goal = request.GET.get('goal', 'explore')
    budget = request.GET.get('budget', 'unsure')
    experience = request.GET.get('experience', 'new')
    goal = goal if goal in GOALS else 'explore'
    budget = budget if budget in BUDGETS else 'unsure'
    experience = experience if experience in EXPERIENCE else 'new'
    message = (f'Hello Boforg, please help me choose a printing setup.\nI want to make: {GOALS[goal]["label"]}.\nBudget: {BUDGETS[budget]}.\nExperience: {EXPERIENCE[experience]}.\nPlease confirm equipment availability, compatible supplies, delivery and any training included.')
    guide_goals = {key: {**value, 'url': shop + ('?' + urlencode({'q': value['query']}) if value['query'] else '?department=printing')} for key, value in GOALS.items()}
    return {
        'home_categories': categories, 'home_featured': featured[:4], 'home_hero': hero, 'packages': packages[:2],
        'home_phone': phone, 'home_phone_display': '+263 786 264 994' if phone == '263786264994' else '+' + phone,
        'home_general_whatsapp': whatsapp_url('Hello Boforg, please help me choose printing equipment or supplies.'),
        'home_software_whatsapp': whatsapp_url('Hello Boforg, I would like to discuss a website or software tools for my business.'),
        'home_share_image': urljoin(settings.PUBLIC_BASE_URL, hero.get_primary_image_url() if hero else '/static/img/1.jpeg'),
        'home_goals': GOALS, 'home_budgets': BUDGETS, 'home_experience': EXPERIENCE,
        'guide_goal': goal, 'guide_budget': budget, 'guide_experience': experience, 'guide_result': guide_goals[goal], 'guide_whatsapp': whatsapp_url(message),
        'home_guide_config': {'goals': guide_goals, 'budgets': BUDGETS, 'experience': EXPERIENCE, 'whatsappNumber': phone},
        'page_description': 'Printing equipment, starter packages and supplies in Harare, Zimbabwe. Explore live prices and find the right setup with Boforg Technologies.',
        'canonical_url': urljoin(settings.PUBLIC_BASE_URL.rstrip('/') + '/', reverse('website:home')),
    }
