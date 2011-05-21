"""
repackage.views
---------------
"""
import commonware

from django.http import (HttpResponse, HttpResponseBadRequest,
        HttpResponseNotAllowed)
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from jetpack.models import SDK
from utils.helpers import get_random_string
from utils import validator

from repackage import tasks

log = commonware.log.getLogger('f.test')


@csrf_exempt
@require_POST
def rebuild(request):
    """Rebuild ``XPI`` file. It can be provided as POST['amo_id'] and
    POST['amo_file'] or it can be included in POST['xpi_file']

    :returns: (JSON) contains one field - hashtag it is later used to download
              the xpi using :method:`xpi.views.check_download` and
              :method:`xpi.views.get_download`
    """
    # validate entries
    if not (request.POST.get('amo_id') and request.POST.get('amo_file')):
        return HttpResponseBadRequest('Please provide access to the XPI file.')

    # XXX: add xpi_file
    # XXX: validate POST data

    hashtag = get_random_string(10)
    # get latest SDK
    sdk = SDK.objects.all()[0]
    # if (when?) choosing POST['sdk_dir'] will be possible
    # sdk = SDK.objects.get(dir=sdk_dir) if sdk_dir else SDK.objects.all()[0]
    sdk_source_dir = sdk.get_source_dir()

    # recognize entry values
    amo_id = request.POST.get('amo_id')
    amo_file = request.POST.get('amo_file')
    package_overrides = {
        'version': request.POST.get('version', None),
        'type': request.POST.get('type', None),
        'fullName': request.POST.get('fullName', None),
        'url': request.POST.get('url', None),
        'description': request.POST.get('description', None),
        'author': request.POST.get('author', None),
        'license': request.POST.get('license', None),
        'lib': request.POST.get('lib', None),
        'data': request.POST.get('data', None),
        'tests': request.POST.get('tests', None),
        'main': request.POST.get('main', None)
    }
    if package_overrides.get('version', None) and not validator.is_valid(
        'alphanum_plus', package_overrides.get('version')):
        return HttpResponseBadRequest('Invalid version format')
    # call download and build xpi task
    tasks.download_and_rebuild.delay(
            amo_id, amo_file, sdk_source_dir, hashtag, package_overrides)
    return HttpResponse('{"hashtag": "%s"}' % hashtag,
            mimetype='application/json')
