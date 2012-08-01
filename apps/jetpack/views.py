"""
Views for the Jetpack application
"""
import commonware.log
import os
import shutil
import codecs
import tempfile
import urllib2
from simplejson import JSONDecodeError

from django.contrib import messages
from django.core.urlresolvers import reverse
from django.db import transaction
from django.views.static import serve
from django.shortcuts import get_object_or_404
from django.http import (HttpResponseRedirect, HttpResponse,
                        HttpResponseForbidden, HttpResponseServerError,
                        Http404, HttpResponseBadRequest)  # , QueryDict
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, InvalidPage
from django.core.exceptions import ValidationError, NON_FIELD_ERRORS
from django.db import IntegrityError, transaction
from django.db.models import Q, ObjectDoesNotExist
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.template.defaultfilters import escape
from django.conf import settings
from django.utils import simplejson
from django.forms.fields import URLField

from base.shortcuts import get_object_with_related_or_404
from utils import validator
from utils.helpers import pathify, render, render_json
from utils.exceptions import parse_validation_messages

from jetpack.package_helpers import (get_package_revision,
        create_package_from_xpi)
from jetpack.models import (Package, PackageRevision, Module, Attachment, SDK,
                           EmptyDir, EDITABLE_EXTENSIONS)
from jetpack.errors import (FilenameExistException, DependencyException,
                            IllegalFilenameException)

from person.models import Profile

log = commonware.log.getLogger('f.jetpack')


def browser(request, page_number=1, type_id=None, username=None):
    """
    Display a list of addons or libraries with pages
    Filter based on the request (type_id, username).
    """
    # calculate which template to use
    template_suffix = ''
    packages = Package.objects.active()

    author = None
    if username:
        try:
            profile = Profile.objects.get_user_by_username_or_nick(username)
        except ObjectDoesNotExist:
            raise Http404
        author = profile.user
        packages = packages.filter(author__pk=author.pk)
        template_suffix = '%s_user' % template_suffix
    if type_id:
        other_type = 'l' if type_id == 'a' else 'a'
        other_packages_number = len(packages.filter(type=other_type))
        packages = packages.filter(type=type_id)
        template_suffix = '%s_%s' % (template_suffix,
                                     settings.PACKAGE_PLURAL_NAMES[type_id])

    packages = packages.sort_recently_active()
    limit = request.GET.get('limit', settings.PACKAGES_PER_PAGE)

    try:
        pager = Paginator(
            packages,
            per_page=limit,
            orphans=1
        ).page(page_number)
    except (EmptyPage, InvalidPage):
        raise Http404

    return render(request,
        'package_browser%s.html' % template_suffix, {
            'pager': pager,
            'single': False,
            'author': author,
            'other_packages_number': other_packages_number
        })


def view_or_edit(request, pk=None, id_number=None, type_id=None,
        revision_number=None, version_name=None, latest=False):
    """
    Edit if user is the author, otherwise view
    """
    revision = get_package_revision(pk, id_number, type_id,
                                    revision_number, version_name, latest)

    edit_available = True
    if revision.package.deleted:
        edit_available = False
        if not request.user.is_authenticated():
            raise Http404
        try:
            Package.objects.active_with_deleted(viewer=request.user).get(
                    pk=revision.package.pk)
        except ObjectDoesNotExist:
            raise Http404

    if not revision.package.active:
        if not request.user.is_authenticated():
            raise Http404
        try:
            Package.objects.active_with_disabled(viewer=request.user).get(
                    pk=revision.package.pk)
        except ObjectDoesNotExist:
            raise Http404

    if (edit_available
            and request.user.is_authenticated()
            and request.user.pk == revision.author.pk):
        return edit(request, revision)
    else:
        return view(request, revision)


@login_required
def edit(request, revision):
    """
    Edit package - only for the author
    """
    if request.user.pk != revision.author.pk:
        # redirecting to view mode without displaying an error
        messages.info(request,
                      "Not sufficient priviliges to edit the source. "
                      "You've been redirected to view mode.")

        return HttpResponseRedirect(
            reverse(
                "jp_%s_revision_details" % revision.package.get_type_name(),
                args=[revision.package.id_number, revision.revision_number])
        )
        #return HttpResponseForbidden('You are not the author of this Package')

    libraries = revision.dependencies.all()
    library_counter = len(libraries)
    sdk_list = None
    if revision.package.is_addon():
        library_counter += 1
        sdk_list = SDK.objects.exclude_disabled(current=revision.sdk.version)

    return render(request,
        "%s_edit.html" % revision.package.get_type_name(), {
            'revision': revision,
            'item': revision.package,
            'single': True,
            'libraries': libraries,
            'library_counter': library_counter,
            'readonly': False,
            'edit_mode': True,
            'sdk_list': sdk_list})


def view(request, revision):
    """
    Show package - read only
    """
    libraries = revision.dependencies.all()
    library_counter = len(libraries)
    if revision.package.is_addon():
        library_counter += 1

    # prepare the json for the Tree
    tree = simplejson.dumps({'Lib': revision.get_modules_tree(),
            'Data': revision.get_attachments_tree(),
            'Plugins': revision.get_dependencies_tree()})

    return render(request,
        "%s_view.html" % revision.package.get_type_name(), {
            'revision': revision,
            'libraries': libraries,
            'library_counter': library_counter,
            'readonly': True,
            'tree': tree
        })


def download_module(request, pk):
    """
    return a JSON with all module info
    """
    module = get_object_with_related_or_404(Module, pk=pk)
    if not module.can_view(request.user):
        log_msg = ("[security] Attempt to download private module (%s) by "
                   "non-owner (%s)" % (pk, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this module.')
    return HttpResponse(module.get_json())


def get_module(request, revision_id, filename):
    """
    return a JSON with all module info
    """
    try:
        revision = PackageRevision.objects.get(pk=revision_id)
        mod = revision.modules.get(filename=filename)
    except PackageRevision.DoesNotExist, Module.DoesNotExist:
        log_msg = 'No such module %s' % filename
        log.error(log_msg)
        raise Http404

    if not mod.can_view(request.user):
        log_msg = ("[security] Attempt to download private module (%s) by "
                   "non-owner (%s)" % (mod, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this module.')
    return HttpResponse(mod.get_json())


@transaction.commit_on_success
@login_required
def copy(request, revision_id):
    """
    Copy package - create a duplicate of the Package, set user as author
    """
    source = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    log.debug('[copy: %s] Copying started from (%s)' % (revision_id, source))

    # save package
    try:
        package = source.package.copy(request.user)
    except IntegrityError, err:
        log.critical(("[copy: %s] Package copy failed") % revision_id)
        return HttpResponseForbidden('You already have a %s with that name' %
                                     escape(source.package.get_type_name()))

    # save revision with all dependencies
    source.save_new_revision(package)
    copied = source
    del source

    log.info('[copy: %s] Copied to %s, (%s)' % (revision_id, copied.pk,
                                                copied.full_name))
    return render_json(request,
        "json/%s_copied.json" % package.get_type_name(),
        {'revision': copied})


@login_required
def disable(request, pk):
    """
    Disable Package and return confirmation
    """
    package = get_object_or_404(Package, pk=pk)
    if request.user.pk != package.author.pk:
        log_msg = 'User %s wanted to disable not his own Package %s.' % (
            request.user, pk)
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                package.get_type_name()))

    package.disable()

    return render_json(request,
            "json/package_disabled.json",
            {'package': package})


@login_required
def activate(request, pk):
    """
    Undelete Package and return confirmation
    """
    package = get_object_or_404(Package, pk=pk)
    if request.user.pk != package.author.pk:
        log_msg = ("[security] Attempt to activate package (%s) by "
                   "non-owner (%s)" % (pk, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                package.get_type_name()))

    package.enable()

    return render_json(request,
            "json/package_activated.json",
            {'package': package})


@login_required
def delete(request, pk):
    """
    Delete Package and return confirmation
    """
    package = get_object_or_404(Package, pk=pk)
    if request.user.pk != package.author.pk:
        log_msg = ("[security] Attempt to delete package (%s) by "
                   "non-owner (%s)" % (pk, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                package.get_type_name()))

    package.delete()

    return render_json(request, "json/package_deleted.json")


@require_POST
@login_required
def add_module(request, revision_id):
    """
    Add new module to the PackageRevision
    """
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to add a module to package (%s) by "
                   "non-owner (%s)" % (id_number, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                revision.package.get_type_name()))

    filename = request.POST.get('filename')

    mod = Module(
        filename=filename,
        author=request.user,
        code="""// %s.js - %s's module
// author: %s""" % (filename, revision.package.full_name,
            request.user.get_profile())
    )
    try:
        mod.save()
        revision.module_add(mod)
    except FilenameExistException, err:
        mod.delete()
        return HttpResponseForbidden(escape(str(err)))

    return render_json(request,
            "json/module_added.json",
            {'revision': revision, 'module': mod})


@require_POST
@login_required
def rename_module(request, revision_id):
    """
    Rename a module in a PackageRevision
    """
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to rename a module to package (%s) by "
                   "non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this Package')

    old_name = request.POST.get('old_filename')
    new_name = request.POST.get('new_filename')

    if old_name == 'main':
        return HttpResponseForbidden(
            'Sorry, you cannot change the name of the main module.'
        )

    if not revision.validate_module_filename(new_name):
        return HttpResponseForbidden(
            ('Sorry, there is already a module in your add-on '
             'with the name "%s". Each module in your add-on '
             'needs to have a unique name.') % new_name
        )

    modules = revision.modules.all()
    module = None

    for mod in modules:
        if mod.filename == old_name:
            module = mod

    if not module:
        log_msg = 'Attempt to rename a non existing module %s from %s.' % (
            old_name, revision_id)
        log.warning(log_msg)
        return HttpResponseForbidden(
            'There is no such module in %s' % escape(
                revision.package.full_name))

    module.filename = new_name
    revision.add_commit_message('module renamed')
    revision.update(module)

    return render_json(request,
            "json/module_renamed.json",
            {'revision': revision, 'module': module})


@require_POST
@login_required
def remove_module(request, revision_id):
    """
    Remove module from PackageRevision
    """
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to remove a module from package (%s) "
                "by non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this Package')

    filenames = request.POST.get('filename').split(',')

    revision.add_commit_message('module removed')
    try:
        removed_modules, removed_dirs = revision.modules_remove_by_path(
                filenames)
    except Module.DoesNotExist:
        log_msg = 'Attempt to delete a non existing module(s) %s from %s.' % (
            str(filenames), revision_id)
        log.warning(log_msg)
        return HttpResponseForbidden(
            'There is no such module in %s' % escape(
                revision.package.full_name))

    return render_json(request,
            "json/module_removed.json",
            {'revision': revision,
            'removed_modules': simplejson.dumps(removed_modules),
            'removed_dirs': simplejson.dumps(removed_dirs)})


@require_POST
@login_required
def add_folder(request, revision_id):
    " adds an EmptyDir to a revision "
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to add a folder to revision (%s) by "
                   "non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this Package')

    foldername, root = (
            request.POST.get('name', ''),
            request.POST.get('root_dir'))

    dir = EmptyDir(name=foldername, author=request.user, root_dir=root)
    try:
        dir.save()
        revision.folder_add(dir)
    except FilenameExistException, err:
        dir.delete()
        return HttpResponseForbidden(escape(str(err)))

    return render_json(request,
            "json/folder_added.json",
            {'revision': revision, 'folder': dir})


@require_POST
@login_required
def remove_folder(request, revision_id):
    " removes an EmptyDir from a revision "
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to remove a folder from revision (%s) "
                "by non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this Package')

    foldername, root = (
            pathify(request.POST.get('name', '')),
            request.POST.get('root_dir'))
    try:
        folder = revision.folders.get(name=foldername, root_dir=root)
    except EmptyDir.DoesNotExist:
        response = None
        if root == 'data':
            response = revision.attachment_rmdir(foldername)
        if not response:
            log_msg = 'Attempt to delete a non existing folder %s from %s.' % (
                foldername, revision_id)
            log.warning(log_msg)
            return HttpResponseForbidden(
                'There is no such folder in %s' % escape(
                    revision.package.full_name))
        revision, removed_attachments, removed_emptydirs = response
        return render_json(request,
                'json/%s_rmdir.json' % root, {
                'revision': revision, 'path': foldername,
                'removed_attachments': simplejson.dumps(removed_attachments),
                'removed_dirs': simplejson.dumps(removed_emptydirs),
                'foldername': foldername})
    else:
        revision.folder_remove(folder)

    return render_json(request,
            "json/folder_removed.json",
            {'revision': revision, 'folder': folder})


@require_POST
@login_required
def switch_sdk(request, revision_id):
    " switch SDK used to create XPI - sdk_id from POST "
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        return HttpResponseForbidden('You are not the author of this Add-on')

    sdk_id = request.POST.get('id', None)
    sdk = get_object_or_404(SDK, id=sdk_id)
    old_sdk = revision.sdk
    log.info('Addon %s (%s) switched from Add-on Kit version %s to %s' % (
        revision.package.full_name, revision.package.id_number,
        old_sdk.version, sdk.version))
    revision.sdk = sdk
    revision.add_commit_message('Switched to Add-on Kit %s' % sdk.version)
    revision.save()

    return render_json(request,
            "json/sdk_switched.json",
            {'revision': revision, 'sdk': sdk,
             'sdk_lib': revision.get_sdk_revision()})


@require_POST
@login_required
def upload_attachment(request, revision_id):
    """ Upload new attachment to the PackageRevision
    """
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    log.debug(revision)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to upload attachment to package (%s) "
                "by non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                revision.package.get_type_name()))

    f = request.FILES.get('upload_attachment')
    filename = request.META.get('HTTP_X_FILE_NAME')

    if not f:
        log_msg = 'Path not found: %s, revision: %s.' % (
            filename, revision_id)
        log.error(log_msg)
        return HttpResponseServerError('Path not found.')

    content = f.read()
    # try to force UTF-8 code, on error continue with original data
    try:
        content = unicode(content, 'utf-8')
    except:
        pass

    try:
        attachment = revision.attachment_create_by_filename(
            request.user, filename, content)
    except ValidationError, e:
        return HttpResponseForbidden(
                'Validation errors.\n%s' % parse_validation_messages(e))
    except Exception, e:
        return HttpResponseForbidden(str(e))

    return render_json(request,
            "json/attachment_added.json",
            {'revision': revision, 'attachment': attachment})


@require_POST
@login_required
def upload_attachments(request, id_number, type_id,
                           revision_number=None, version_name=None):
    """ Upload new attachments to the PackageRevision
    """
    revision = get_package_revision(None, id_number, type_id, revision_number,
                                    version_name)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to upload attachment to package (%s) "
                "by non-owner (%s)" % (id_number, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                revision.package.get_type_name()))

    content = request.raw_post_data
    filename = request.META.get('HTTP_X_FILE_NAME')

    if not filename:
        log_msg = 'Path not found: %s, package: %s.' % (
            filename, id_number)
        log.error(log_msg)
        return HttpResponseServerError('Path not found.')

    try:
        attachment = revision.attachment_create_by_filename(
            request.user, filename, content)
    except ValidationError, e:
        return HttpResponseForbidden(
                'Validation errors.\n%s' % parse_validation_messages(e))
    except Exception, e:
        return HttpResponseForbidden(str(e))

    return render_json(request,
            "json/attachment_added.json",
            {'revision': revision, 'attachment': attachment})


@require_POST
@login_required
def add_empty_attachment(request, id_number, type_id,
                           revision_number=None, version_name=None):
    """ Add new empty attachment to the PackageRevision
    """
    revision = get_package_revision(None, id_number, type_id, revision_number,
                                    version_name)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to add attachment to package (%s) by "
                   "non-owner (%s)" % (id_number, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                revision.package.get_type_name()))

    filename = request.POST.get('filename', False)

    if not filename:
        log_msg = 'Path not found: %s, package: %s.' % (
            filename, id_number)
        log.error(log_msg)
        return HttpResponseServerError('Path not found.')

    try:
        attachment = revision.attachment_create_by_filename(request.user,
                filename, '')
    except ValidationError, e:
        return HttpResponseForbidden(
                'Validation errors.\n%s' % parse_validation_messages(e))
    except Exception, e:
        return HttpResponseForbidden(str(e))

    return render_json(request,
            "json/attachment_added.json",
            {'revision': revision, 'attachment': attachment})


@require_POST
@login_required
def revision_add_attachment(request, pk):
    """Add attachment, download if necessary
    """
    revision = get_object_or_404(PackageRevision, pk=pk)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to add attachment to package (%s) by "
                   "non-owner (%s)" % (revision.package, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                revision.package.get_type_name()))
    url = request.POST.get('url', None)
    filename = request.POST.get('filename', None)
    if not filename or filename == "":
        log.error('Trying to create an attachment without name')
        return HttpResponseBadRequest('Path not found.')
    content = ''
    if url:
        log.info(('[%s] Preparing to download %s as an attachment of '
            'PackageRevision %d') % (filename, url, revision.pk))
        # validate url
        field = URLField(verify_exists=True)
        encoding = request.POST.get('force_contenttype', False)
        try:
            url = field.clean(url)
        except ValidationError, err:
            log.warning('[%s] Invalid url provided\n%s' % (url,
                '\n'.join(err.messages)))
            return HttpResponseBadRequest(("Loading attachment failed\n"
                "%s") % parse_validation_messages(err))
        except Exception, err:
            log.warning('[%s] Exception raised\n%s' % (url, str(err)))
            return HttpResponseBadRequest(str(err))
        att = urllib2.urlopen(url, timeout=settings.URLOPEN_TIMEOUT)
        # validate filesize
        att_info = att.info()
        if 'content-length' in att_info.dict:
            att_size = int(att_info.dict['content-length'])
            if att_size > settings.ATTACHMENT_MAX_FILESIZE:
                log.warning('[%s] File is too big (%db)' % (url, att_size))
                return HttpResponseBadRequest("Loading attachment failed\n"
                        "File is too big")
        # download attachment's content
        log.debug('[%s] Downloading' % url)
        content = att.read(settings.ATTACHMENT_MAX_FILESIZE + 1)
        # work out the contenttype
        basename, ext = os.path.splitext(filename)
        unicode_contenttypes = ('utf-8',)
        ext = ext.split('.')[1].lower() if ext else None
        if not encoding:
            encoding = att.headers['content-type'].split('charset=')[-1]
        if encoding not in unicode_contenttypes and ext in EDITABLE_EXTENSIONS:
            log.info('[%s] Forcing the "utf-8" encoding from '
                    '"%s"' % (url, encoding))
            encoding = 'utf-8'
        # convert to unicode if needed
        if encoding in unicode_contenttypes:
            content = unicode(content, encoding)
        if len(content) >= settings.ATTACHMENT_MAX_FILESIZE + 1:
            log.warning('[%s] Downloaded file is too big' % url)
            return HttpResponseBadRequest("Loading attachment failed\n"
                    "File is too big")
        log.info('[%s] Downloaded %db, encoding: %s' % (url, len(content),
                                                        encoding))
        att.close()
    try:
        attachment = revision.attachment_create_by_filename(
            request.user, filename, content)
    except ValidationError, err:
        log.warning("[%s] Validation error.\n%s" % (filename, str(err)))
        return HttpResponseForbidden(
                'Validation error.\n%s' % parse_validation_messages(err))
    except Exception, err:
        log.warning("[%s] Exception raised\n%s" % (filename, str(err)))
        return HttpResponseForbidden(str(err))

    return render_json(request,
            "json/attachment_added.json",
            {'revision': revision, 'attachment': attachment})


@require_POST
@login_required
@transaction.commit_on_success
def rename_attachment(request, revision_id):
    """
    Rename an attachment in a PackageRevision
    """
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to rename attachment in revision (%s) "
                "by non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this Package')

    uid = request.POST.get('uid', '').strip()
    try:
        attachment = revision.attachments.get(pk=uid)
    except:
        log_msg = ('Attempt to rename a non existing attachment. attachment: '
                   '%s, revision: %s.' % (uid, revision))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'There is no such attachment in %s' % escape(
                revision.package.full_name))

    new_name = request.POST.get('new_filename')
    new_ext = request.POST.get('new_ext') or attachment.ext

    if not revision.validate_attachment_filename(new_name, new_ext):
        return HttpResponseForbidden(
            ('Sorry, there is already an attachment in your add-on '
             'with the name "%s.%s". Each attachment in your add-on '
             'needs to have a unique name.') % (new_name, attachment.ext)
        )
    attachment.filename = new_name
    attachment.ext = new_ext
    try:
        attachment = revision.update(attachment)
    except ValidationError, err:
        return HttpResponseForbidden(str(err))

    return render_json(request,
            "json/attachment_renamed.json",
            {'revision': revision, 'attachment': attachment})


@require_POST
@login_required
def rmdir(request, pk, target, path):
    """
    Remove attachment from PackageRevision
    """
    revision = get_object_or_404(PackageRevision, pk=pk)
    if target not in ['data', 'lib']:
        return HttpResponseForbidden
    if target == 'lib':
        return HttpResponseForbidden('not supported yet')

    revision.attachment_rmdir(path) if target == 'data' else \
            revision.modules_rmdir(path)
    return render_json(request,
            '%s_rmdir.json' % target, {'revision': revision, 'path': path})


@require_POST
@login_required
def remove_attachment(request, revision_id):
    """
    Remove attachment from PackageRevision
    """
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ('[security] Attempt to remove attachment from revision '
                '(%s) by non-owner (%s)' % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this Package')

    uid = request.POST.get('uid', '').strip()
    attachment = get_object_with_related_or_404(Attachment,
                                                pk=uid, revisions=revision)

    if not attachment:
        log_msg = ('Attempt to remove a non existing attachment. attachment: '
                   '%s, revision: %s.' % (uid, revision_id))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'There is no such attachment in %s' % escape(
                revision.package.full_name))

    revision.attachment_remove(attachment)

    return render_json(request,
            "json/attachment_removed.json",
            {'revision': revision, 'attachment': attachment})


def download_attachment(request, uid):
    """
    Display attachment from PackageRevision
    """
    attachment = get_object_with_related_or_404(Attachment, id=uid)
    if not attachment.can_view(request.user):
        log_msg = ("[security] Attempt to download private attachment (%s) by "
                   "non-owner (%s)" % (uid, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this attachment.')
    response = serve(request, attachment.path,
                     settings.UPLOAD_DIR, show_indexes=False)
    response['Content-Disposition'] = 'filename=%s.%s' % (
            attachment.filename, attachment.ext)
    return response


@require_POST
@login_required
def save(request, revision_id, type_id=None):
    """
    Save package and modules
    @TODO: check how dynamic module loading affects save
    """

    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to save package (%s) by "
                   "non-owner (%s)" % (revision.pk,
                       request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this Package')

    save_revision = False
    save_package = False
    start_version_name = revision.version_name
    start_revision_message = revision.message

    response_data = {}

    package_full_name = request.POST.get('full_name', False)
    jid = request.POST.get('jid', None)
    version_name = request.POST.get('version_name', False)

    if jid and not validator.is_valid(
        'alphanum_plus', jid):
        return HttpResponseForbidden(escape(
            validator.get_validation_message('alphanum_plus')))

    # validate package_full_name and version_name
    if version_name and not validator.is_valid(
        'alphanum_plus', version_name):
        return HttpResponseForbidden(escape(
            validator.get_validation_message('alphanum_plus')))

    # here we're checking if the *current* full_name is different than the
    # revision's full_name
    if package_full_name and package_full_name != revision.package.full_name:
        try:
            revision.set_full_name(package_full_name)
        except ValidationError:
            return HttpResponseForbidden(escape(
                validator.get_validation_message('alphanum_plus_space')))
        except IntegrityError:
            return HttpResponseForbidden(
                'You already have a %s with that name' % escape(
                    revision.package.get_type_name())
                )
        else:
            save_package = True
            save_revision = True
            response_data['full_name'] = package_full_name

    package_description = request.POST.get('package_description', False)
    if package_description:
        save_package = True
        revision.package.description = package_description
        response_data['package_description'] = package_description

    extra_json = request.POST.get('package_extra_json')
    if extra_json is not None:
        # None means it wasn't submitted. We want to accept blank strings.
        save_revision = True
        try:
            revision.set_extra_json(extra_json, save=False)
        except JSONDecodeError:
            return HttpResponseBadRequest(
                    'Extra package properties were invalid JSON.')
        except IllegalFilenameException, e:
            return HttpResponseBadRequest(str(e))
        response_data['package_extra_json'] = extra_json


    changes = []
    for mod in revision.modules.all():
        if request.POST.get(mod.filename, False):
            code = request.POST[mod.filename]
            if mod.code != code:
                mod.code = code
                changes.append(mod)

    for att in revision.attachments.all():
        uid = str(att.pk)
        if request.POST.get(uid):
            att.data = request.POST[uid]
            if att.changed():
                changes.append(att)

    attachments_changed = {}
    if save_revision or changes:
        try:
            revision.save()
        except ValidationError, err:
            return HttpResponseForbidden(
                'Validation error.\n%s' % parse_validation_messages(err))

    if changes:
        attachments_changed = simplejson.dumps(
                revision.updates(changes, save=False))

    revision_message = request.POST.get('revision_message', False)
    if revision_message and revision_message != start_revision_message:
        revision.message = revision_message
        # save revision message without changeing the revision
        super(PackageRevision, revision).save()
        response_data['revision_message'] = revision_message

    if version_name and version_name != start_version_name \
        and version_name != revision.package.version_name:
        save_package = False
        try:
            revision.set_version(version_name)
        except Exception, err:
            return HttpResponseForbidden(escape(err.__str__()))

    if jid:
        try:
            Package.objects.get(jid=jid)
        except Package.DoesNotExist:
            pass
        else:
            return HttpResponseForbidden(('Package with JID "%s" already '
                    'exists in the Builder') % jid)
        revision.package.jid = jid
        response_data['jid'] = jid
        save_package = True

    if save_package:
        revision.package.save()
        response_data['name'] = revision.package.name

    response_data['version_name'] = revision.get_version_name_only()

    if save_revision or changes:
        revision.update_commit_message(True)

    return render_json(request, "json/package_saved.json", locals())


@login_required
@transaction.commit_on_success
def create(request, type_id):
    """
    Create new Package (Add-on or Library)
    Usually no full_name used
    """

    full_name = request.POST.get("full_name", None)
    description = request.POST.get("description", "")

    item = Package(
        author=request.user,
        full_name=full_name,
        description=description,
        type=type_id)

    item.save()

    return HttpResponseRedirect(reverse('jp_latest', args=[item.pk]))


@require_POST
@login_required
def upload_xpi(request):
    """
    upload XPI and create Addon and eventual Libraries
    """
    try:
        xpi = request.FILES['xpi']
    except KeyError:
        log.warning('No file "xpi" posted')
        return HttpResponseForbidden('No xpi supplied.')

    temp_dir = tempfile.mkdtemp()
    path = os.path.join(temp_dir, xpi.name)
    xpi_file = codecs.open(path, mode='wb+')
    for chunk in xpi.chunks():
        xpi_file.write(chunk)
    xpi_file.close()
    try:
        addon = create_package_from_xpi(path, request.user)
    except Exception, err:
        log.warning("Bad file %s" % str(err))
        return HttpResponseForbidden('Wrong file')
    os.remove(path)
    shutil.rmtree(temp_dir)
    return HttpResponseRedirect(addon.get_absolute_url())
    # after front-end will support interactive upload
    return HttpResponse(simplejson.dumps({'reload': addon.get_absolute_url()}))


@login_required
def library_autocomplete(request):
    """
    'Live' search by name
    """
    from search.helpers import package_query
    from elasticutils import F

    q = request.GET.get('q')
    limit = request.GET.get('limit')
    try:
        limit = int(limit)
    except:
        limit = settings.LIBRARY_AUTOCOMPLETE_LIMIT

    ids = (settings.MINIMUM_PACKAGE_ID, settings.MINIMUM_PACKAGE_ID - 1)
    notAddonKit = ~(F(id_number=ids[0]) | F(id_number=ids[1]))
    onlyMyPrivateLibs = (F(active=True) | F(author=request.user.id))
    
    try:
        qs = (Package.search().query(or_=package_query(q)).filter(type='l')
                .filter(notAddonKit).filter(onlyMyPrivateLibs))
        found = qs[:limit]
    except Exception, ex:
        log.exception('Library autocomplete error')
        found = []

    return render_json(request,
            'json/library_autocomplete.json', {'libraries': found})


@require_POST
@login_required
def assign_library(request, revision_id):
    " assign library to the package "
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to assign library to revision (%s) by "
                   "non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden('You are not the author of this Package')

    # TODO: make linking work with library_id instead of id number
    library = get_object_or_404(
        Package, type='l', id_number=request.POST['id_number'])
    if request.POST.get('use_latest_version', False):
        lib_revision = library.version
    else:
        lib_revision = library.latest

    try:
        revision.dependency_add(lib_revision)
    except Exception, err:
        return HttpResponseForbidden(str(err))

    lib_revision_url = lib_revision.get_absolute_url() \
        if request.user.pk == lib_revision.pk \
        else lib_revision.get_absolute_url()

    return render_json(request,
            'json/library_assigned.json', {
                'revision': revision,
                'library': library,
                'lib_revision': lib_revision,
                'lib_revision_url': lib_revision_url})


@require_POST
@login_required
def remove_library(request, revision_id):
    " remove dependency from the library provided via POST "
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to remove library from revision (%s) by "
                   "non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                revision.package.get_type_name()))

    # TODO: make unlinking work with library_id instead of id number
    lib_id_number = request.POST.get('id_number')
    library = get_object_or_404(Package, id_number=lib_id_number)

    try:
        revision.dependency_remove_by_id_number(lib_id_number)
    except Exception, err:
        return HttpResponseForbidden(escape(err.__str__()))

    return render_json(request,
            'json/dependency_removed.json',
            {'revision': revision, 'library': library})


@require_POST
@login_required
def update_library(request, revision_id):
    " update a dependency to a certain version "
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if request.user.pk != revision.author.pk:
        log_msg = ("[security] Attempt to update library in revision (%s) by "
                   "non-owner (%s)" % (revision_id, request.user))
        log.warning(log_msg)
        return HttpResponseForbidden(
            'You are not the author of this %s' % escape(
                revision.package.get_type_name()))

    # TODO: make updating work with library_id instead of id number
    lib_id_number = request.POST.get('id_number')
    lib_revision = request.POST.get('revision')

    library = get_object_or_404(PackageRevision, pk=lib_revision,
            package__id_number=lib_id_number)

    try:
        revision.dependency_update(library)
    except DependencyException, err:
        return HttpResponseForbidden(escape(err.__str__()))

    return render_json(request,
            'json/library_updated.json', {
                'revision': revision,
                'library': library.package,
                'lib_revision': library})


@login_required
def latest_dependencies(request, revision_id):
    revision = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    out_of_date = revision.get_outdated_dependency_versions()

    return render_json(request,
            'json/latest_dependencies.json', {'revisions': out_of_date})


@never_cache
def get_revisions_list_html(request, revision_id):
    " returns revision list to be displayed in the modal window "
    current = get_object_with_related_or_404(PackageRevision, pk=revision_id)
    if not current.package.can_view(request.user):
        raise Http404
    revisions = current.package.revisions.all()
    revision_number = int(current.revision_number)
    return render(request,
        '_package_revisions_list.html', {
            'package': current.package,
            'revisions': revisions,
            'revision_number': revision_number,
            'current': current})


@never_cache
def get_latest_revision_number(request, package_id):
    """ returns the latest revision number for given package """
    package = get_object_or_404(Package, id_number=package_id)
    if not package.can_view(request.user):
        raise Http404
    return HttpResponse(simplejson.dumps({
        'revision_number': package.latest.revision_number}))


@never_cache
def get_revision_modules_list(request, pk):
    """returns JSON object with all modules which will be exported to XPI
    """
    revision = get_object_or_404(PackageRevision, pk=pk)
    return HttpResponse(simplejson.dumps(revision.get_module_names()),
                        mimetype="application/json")


@never_cache
def get_revision_conflicting_modules_list(request, pk):
    """returns JSON object with all modules which will be exported to XPI
    """
    revision = get_object_or_404(PackageRevision, pk=pk)
    return HttpResponse(simplejson.dumps(
        revision.get_conflicting_module_names()), mimetype="application/json")
