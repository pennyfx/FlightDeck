from django.core.cache import cache

from elasticutils import S, F
from jetpack.models import Package
from jingo import register
import jinja2


def package_search(searchq='', user=None, score_on=None, **filters):
    """This provides some sane defaults to filter on when searching Packages"""

    # This is a filtered query, that says we want to do a query, but not have
    # to deal with version_text='initial' or 'copy'
    notInitialOrCopy = ~(F(version_name='initial') | F(version_name='copy')) 

    qs = Package.search().values_obj('copies_count','times_depended',
            'activity','size').filter(notInitialOrCopy, 
            **filters).filter(F(active=True))

    # Add type facet (minus any type filter)
    facetFilter = dict((k, v) for k, v in filters.items() if k != 'type')
    if facetFilter:
        facetFilter = notInitialOrCopy & F(**facetFilter)
    else:
        facetFilter = notInitialOrCopy
    qs = qs.facet(types={'terms': {'field': 'type'},
                'facet_filter': facetFilter.filters})

    if searchq:
        qs = qs.query(or_=package_query(searchq))

    if user and user.is_authenticated():
        qs = qs.facet(author={'terms': {
            'field': 'author',
            'script':'term == %d ? true : false' % user.id}
        })
   
    return qs


def package_query(q):
    # 1. Prefer name text matches first (boost=3).
    # 2. Try fuzzy matches on name ("git hub" => github) (boost=2).
    # 3. Try query as a the start of name (boost=1.5)
    # 4. Try text match inside description (boost=0.8)
    return dict(full_name__text={'query': q, 'boost': 3},
                full_name__fuzzy={'value': q, 'boost': 2, 'prefix_length': 4},
                full_name__startswith={'value': q, 'boost': 1.5},
                description__text={'query': q, 'boost': 0.8})


ACTIVITY_MAP = {
    '0': 0,   #dead
    '1': 0.1, #stale
    '2': 0.2, #low
    '3': 0.4, #moderate
    '4': 0.6, #high
    '5': 0.8, #fresh
}

ACTIVITY_MAP_UI = {
    '0': 'Inactive',
    '1': 'Stale',
    '2': 'Low',
    '3': 'Moderate',
    '4': 'High',
    '5': 'Rockin\''
}

@register.function
def get_activity_level_UI(activity):
    """ Takes activity score and turns it into UI value """    
    if not activity:
        return 'Inactive'
    
    scale = get_activity_scale();
    last = 0
    for i,v in scale.iteritems():    
        if activity > v and i >= last:    
            last = i    
    return ACTIVITY_MAP_UI.get(last)

def get_activity_scale():
    avg = _get_average_activity()
    # average should be considered 1/5 (Low)
    # so total percentage is triple the average
    total = avg * 5

    act_map = dict((k, v*total) for k, v in ACTIVITY_MAP.items())

    return act_map


ACTIVITY_CACHE_KEY = 'search:activity:average'

def _get_average_activity():    
    average = cache.get(ACTIVITY_CACHE_KEY)
    if average:
        return average
    # TODO: ES has statistical facet that can provide average, but I couldn't
    # get it working.
    
    qs = Package.search().filter(activity__gt=0.001)
    values = qs.values('activity')[:qs.count()]
    
    num = len(values)
    
    if num > 0:
        average = sum(v[1] for v in values) / num
    else:
        average = 0.2
    
    cache.set(ACTIVITY_CACHE_KEY, average, 60*60*24)
    return average


@jinja2.contextfunction
@register.function
def select_selected(context, value):
    bits = value.split('=');
    if context['request'].GET.get(bits[0]) == bits[1] or context.get('query')[bits[0]] == bits[1]:
        return 'selected=selected'
    else:
        return ''