# coding=utf-8
import os
import tempfile
import commonware
import json
import shutil
import simplejson
import hashlib

from datetime import datetime
from mock import Mock
from nose.tools import eq_
from nose import SkipTest
from test_utils import TestCase

from django.contrib.auth.models import User
from django.conf  import settings
from django.core.urlresolvers import reverse
from django.forms.fields import URLField

from jetpack.models import Package, PackageRevision, Attachment
from jetpack.tests.test_views import next_revision
from jetpack.errors import FilenameExistException, IllegalFilenameException

log = commonware.log.getLogger('f.test')

class AttachmentTest(TestCase):
    """Testing attachment methods."""

    fixtures = ('core_sdk', 'users')

    def setUp(self):
        self.author = User.objects.get(username='john')

        self.old = settings.UPLOAD_DIR
        settings.UPLOAD_DIR = tempfile.mkdtemp()
        self.tempdir = tempfile.mkdtemp()

        # Simulating upload.
        self.attachment = Attachment.objects.create(
            filename='test_filename',
            ext='txt',
            author=self.author
        )
        self.attachment.create_path()
        self.attachment.data = 'test'
        self.attachment.write()

        self.path = self.attachment.path

        self.urlfield_clean_backup = URLField.clean

    def tearDown(self):
        shutil.rmtree(settings.UPLOAD_DIR)
        shutil.rmtree(self.tempdir)
        settings.UPLOAD_DIR = self.old
        URLField.clean = self.urlfield_clean_backup

    def test_export_file(self):
        destination = self.tempdir
        filename = '%s.%s' % (self.attachment.filename, self.attachment.ext)
        filename = os.path.join(destination, filename)
        self.attachment.export_file(destination)
        assert os.path.isfile(filename)

    def test_create_attachment_with_utf_content(self):
        self.attachment.data = u'ą'
        self.attachment.write()
        destination = self.tempdir
        filename = '%s.%s' % (self.attachment.filename, self.attachment.ext)
        filename = os.path.join(destination, filename)
        self.attachment.export_file(destination)
        assert os.path.isfile(filename)
        f = open(filename, 'r')
        eq_(f.read(), "ą")
        f.close()

    def test_update_attachment_with_utf_content_from_view(self):
        addon = Package.objects.create(
                type='a',
                author=self.author)
        addon.latest.attachment_add(self.attachment)
        self.author.set_password('secure')
        self.author.save()
        self.client.login(username=self.author.username, password='secure')
        response = self.client.post(addon.latest.get_save_url(), {
            self.attachment.pk: u'ą'})
        eq_(response.status_code, 200)
        addon = Package.objects.get(author=self.author)
        eq_(addon.latest.revision_number, 2)
        eq_(addon.latest.attachments.get().read(), u'ą')

    def test_attachment_with_utf_from_web(self):
        url = "file://%s/apps/jetpack/tests/jquery-1.6.4.min.js" % settings.ROOT
        URLField.clean = Mock(return_value=url)
        addon = Package.objects.create(
                type='a',
                author=self.author)
        self.author.set_password('secure')
        self.author.save()
        self.client.login(username=self.author.username, password='secure')
        response = self.client.post(addon.latest.get_add_attachment_url(), {
            'url': url,
            'filename': 'jquery-1.6.4.min.js',
            'force_contenttype': 'utf-8'})
        eq_(response.status_code, 200)
        addon = Package.objects.get(author=self.author)
        eq_(addon.latest.revision_number, 1)
        attachment = addon.latest.attachments.get()
        eq_("jquery-1.6.4.min", attachment.filename)
        assert os.path.isfile(attachment.get_file_path())
        assert attachment.read()

        # unicode forced for all html, css, txt and js files
        url = "file://%s/apps/jetpack/tests/jquery-1.6.4.min.js" % settings.ROOT
        URLField.clean = Mock(return_value=url)
        response = self.client.post(addon.latest.get_add_attachment_url(), {
            'url': url,
            'filename': 'jquery-1.6.4.min-next.js'})
        eq_(response.status_code, 200)
        addon = Package.objects.get(author=self.author)
        eq_(addon.latest.revision_number, 2)
        attachment = addon.latest.attachments.latest('pk')
        eq_("jquery-1.6.4.min-next", attachment.filename)
        assert os.path.isfile(attachment.get_file_path())
        assert attachment.read()


    def test_create_image_attachment(self):
        " test simply shouldn't raise any errors "
        image_path = os.path.join(settings.ROOT,
                                  'apps/jetpack/tests/sample_image.png')
        f = open(image_path, 'r')
        self.attachment.data = f.read()
        f.close()
        self.attachment.ext = 'png'
        self.attachment.write()

        self.attachment.read()

    def test_illegal_filename(self):
        att = Attachment(filename='.../...///foo.js')
        self.assertRaises(IllegalFilenameException, att.save)

        att = Attachment(filename='/bin/python')
        self.assertRaises(IllegalFilenameException, att.save)



class TestViews(TestCase):
    fixtures = ['mozilla_user', 'users', 'core_sdk', 'packages']

    def setUp(self):
        if not os.path.exists(settings.UPLOAD_DIR):
            os.makedirs(settings.UPLOAD_DIR)

        self.tempdir = tempfile.mkdtemp()

        self.author = User.objects.get(username='john')
        self.author.set_password('password')
        self.author.save()

        self.package = self.author.packages_originated.addons()[0:1].get()
        self.revision = self.package.revisions.all()[0]
        self.revision_number = 0

        self.add_url = self.get_add_url(self.revision)
        self.upload_url = self.get_upload_url(self.revision.revision_number)
        self.change_url = self.get_change_url(self.revision.revision_number)
        self.client.login(username=self.author.username, password='password')

    def tearDown(self):
        shutil.rmtree(self.tempdir)

    def test_attachment_error(self):
        res = self.client.post(self.add_url, {})
        eq_(res.status_code, 400)

    def add_one(self, data = 'foo', filename='some.txt'):
        self.upload(self.get_upload_url(self.revision.revision_number), data, filename)
        self.revision = next_revision(self.revision)
        return self.revision

    def get_upload_url(self, revision_number):
        revision = self.package.revisions.get(revision_number=revision_number)
        return reverse('jp_package_revision_upload_attachment',
                args=[revision.pk])

    def get_add_url(self, revision):
        args = [revision.pk]
        return reverse('jp_revision_add_attachment', args=args)

    def get_change_url(self, revision_number):
        revision = self.package.revisions.get(revision_number=revision_number)
        return reverse('jp_revision_save', args=[revision.pk])

    def get_delete_url(self, revision_number):
        revision = self.package.revisions.get(revision_number=revision_number)
        return reverse('jp_package_revision_remove_attachment',
                args=[revision.pk])

    def get_revision(self):
        return PackageRevision.objects.get(pk=self.revision.pk)

    def upload(self, url, data, filename):
        # A post that matches the JS and uses raw_post_data.
        attachment = os.path.join(self.tempdir, 'upload_attachment')
        f = open(attachment, 'w')
        f.write(data)
        f.close()
        f = open(attachment, 'r')
        resp = self.client.post(url, { 'upload_attachment': f },
                                HTTP_X_FILE_NAME=filename)
        f.close()
        os.unlink(attachment)
        return resp

    def test_attachment_path(self):
        res = self.upload(self.upload_url, 'foo', 'some.txt')
        eq_(res.status_code, 200)
        revision = PackageRevision.objects.get(package=self.package,
                                               revision_number=1)
        att = revision.attachments.all()[0]
        bits = att.path.split(os.path.sep)
        now = datetime.now()
        eq_(bits[-4:-1], now.strftime('%Y-%m-%d').split('-'))


    def test_attachment_add_read(self):
        res = self.upload(self.upload_url, 'foo', 'some.txt')
        eq_(res.status_code, 200)

        revision = PackageRevision.objects.get(package=self.package,
                                               revision_number=1)
        eq_(revision.attachments.count(), 1)
        eq_(revision.attachments.all()[0].read(), 'foo')

    def test_attachment_add(self):
        res = self.upload(self.upload_url, 'foo', 'some.txt')
        eq_(res.status_code, 200)
        json.loads(res.content)

        revision = PackageRevision.objects.get(package=self.package,
                                               revision_number=1)
        eq_(revision.attachments.count(), 1)

    def test_attachment_default_extension(self):
        revision = self.add_one(data='foo', filename='some')
        eq_(revision.attachments.all()[0].ext, 'js')

    def test_attachment_large(self):
        raise SkipTest()
        # A test for large attachments... really slow things
        # down, so before you remove the above, clean this up
        # or drop down the file size limit.
        import StringIO
        temp = StringIO.StringIO()
        for x in range(0, 1024 * 32):
            temp.write("x" * 1024)

        self.upload(self.upload_url, temp.getvalue(), 'some-big-file.txt')

    def test_attachment_same_fails(self):
        revision = self.add_one()

        # upload view
        res1 = self.upload(self.get_upload_url(1), 'foo bar', 'some.txt')
        eq_(res1.status_code, 403)

        # add empty view
        res2 = self.client.post(self.get_add_url(revision), {
                    "filename": "some.txt"})
        eq_(res2.status_code, 403)

    def test_external_url_fails(self):
        revision = self.add_one()

        # invalid url
        response = self.client.post(self.get_add_url(revision), {
                    "filename": "some.txt",
                    "url": "abc"})
        eq_(response.status_code, 400)
        # not existing url
        response = self.client.post(self.get_add_url(revision), {
                    "filename": "some.txt",
                    "url": "http://notexistingurl.pl/some.txt"})
        eq_(response.status_code, 400)
        # malicious input
        response = self.client.post(self.get_add_url(revision), {
                    "filename": "",
                    "url": "http://example.com/"})
        eq_(response.status_code, 400)
        # TODO: Add tests for:
        #       * no content-length header
        #       * content-length <= 0
        #       * content-length > settings.ATTACHMENT_MAX_FILESIZE
        #       * status_code == 200

    def test_attachment_revision_count(self):
        revisions = PackageRevision.objects.filter(package=self.package)
        eq_(revisions.count(), 1)
        self.test_attachment_add()
        eq_(revisions.count(), 2)
        # Double check that adding a revision does not create a new version.

    def test_attachment_same_change(self):
        self.test_attachment_add()
        revision = PackageRevision.objects.get(package=self.package,
                                               revision_number=1)
        eq_(revision.attachments.count(), 1)

        data = {revision.attachments.all()[0].get_uid: 'foo bar'}
        res = self.client.post(self.get_change_url(1), data)
        eq_(res.status_code, 200)

        eq_(revision.attachments.all()[0].read(), 'foo')

        revision = PackageRevision.objects.get(package=self.package,
                                               revision_number=2)
        eq_(revision.attachments.count(), 1)
        eq_(revision.attachments.all()[0].read(), 'foo bar')

    def test_attachment_two_files(self):
        revision = self.add_one()
        assert revision.attachments.count(), 1

        self.upload(self.upload_url, 'foo', 'some-other.txt')
        assert revision.attachments.count(), 2

    def test_paths(self):
        revision = self.add_one()

        data = {revision.attachments.all()[0].get_uid: 'foo bar'}
        self.client.post(self.get_change_url(1), data)
        atts = Attachment.objects.filter(revisions__package=self.package)

        hasht = hashlib.md5('sometxt').hexdigest()

        assert atts[0].get_file_path().endswith('%s-%s' % (atts[0].pk, hasht))
        assert atts[1].get_file_path().endswith('%s-%s' % (atts[1].pk, hasht))

    def test_attachment_remove(self):
        revision = self.add_one()

        data = {'uid': revision.attachments.all()[0].get_uid}
        self.client.post(self.get_delete_url(1), data)

        revision = PackageRevision.objects.get(package=self.package,
                                               revision_number=2)
        assert not revision.attachments.all().count()

    def test_fake_attachment_remove(self):
        self.add_one()

        data = {'uid': '1337'}
        resp = self.client.post(self.get_delete_url(1), data)

        eq_(resp.status_code, 404)

    def test_attachment_with_utf_upload(self):
        file_path = os.path.join(settings.ROOT,
                                  'apps/jetpack/tests/jquery-1.6.4.min.js')

        url = self.get_upload_url(self.revision.revision_number)
        with open(file_path, 'r') as f:
            response = self.client.post(url, { 'upload_attachment': f },
                                    HTTP_X_FILE_NAME='jquery-1.6.4.min.js')
        eq_(response.status_code, 200)

    def test_attachment_rename(self):
        revision = self.add_one()
        old_uid = revision.attachments.all()[0].get_uid
        revision = PackageRevision.objects.get(package=self.package,
                                               revision_number=1)
        response = self.client.post(revision.get_rename_attachment_url(), {
                    'new_filename': 'xxx',
                    'new_ext': 'txt',
                    'uid': old_uid})
        eq_(response.status_code, 200)
        content = simplejson.loads(response.content)
        assert content.has_key('uid')
        assert content['uid'] != old_uid

        revision = next_revision(revision)
        eq_(revision.attachments.count(), 1)

        # use a not allowed extension length
        old_rev_no = revision.package.revisions.count()
        old_uid = revision.attachments.all()[0].get_uid
        response = self.client.post(revision.get_rename_attachment_url(), {
                    'new_filename': 'xxx',
                    'new_ext': '01234567890987654321',
                    'uid': old_uid})
        eq_(response.status_code, 403)
        # XXX: I've added transaction.on_success to the view, but this is
        #      still creating a new revision. Check is needed if that is still
        #      the case on test server.
        #eq_(old_rev_no, revision.package.revisions.count())

    def test_attachment_save(self):
        # back-end responds with
        # 'attachments_updated': {old_uid: {uid: new_uid}}
        revision = self.add_one()
        old_uid = str(revision.attachments.all()[0].pk)
        data = {old_uid: 'somecontent'}
        response = simplejson.loads(
                self.client.post(self.get_change_url(1), data).content)
        revision = PackageRevision.objects.get(package=self.package,
                                               revision_number=2)
        new_uid = revision.attachments.get().pk
        eq_(response['attachments_changed'][old_uid]['uid'], str(new_uid))
        assert old_uid != str(new_uid)
        eq_(revision.attachments.all().count(), 1)

    def test_attachment_unwanted_duplication(self):
        # https://bugzilla.mozilla.org/show_bug.cgi?id=633939#c2
        # create attachment
        filename = "html/test"
        response = simplejson.loads(
                self.client.post(self.add_url, {
                    "filename": "%s.html" % filename}).content)
        revision1 = self.package.revisions.filter(
                revision_number=response['revision_number']).get()
        eq_(revision1.revision_number, 1)
        att_uid = response['uid']
        # add content to attachment
        content = "some content"
        response = simplejson.loads(
                self.client.post(
                    self.get_change_url(revision1.revision_number),
                    {att_uid: content}
                    ).content)
        revision2 = self.package.revisions.filter(
                revision_number=response['revision_number']).get()
        eq_(revision2.revision_number, 2)
        att = revision2.attachments.filter(filename=filename).get()
        response = self.client.get(reverse('jp_attachment', args=[att.pk]))
        eq_(response.content, content)
        # updating the attachment in revision1
        content2 = "some other content"
        response = simplejson.loads(
                self.client.post(
                    self.get_change_url(revision1.revision_number),
                    {att_uid: content2}
                    ).content)
        revision3 = self.package.revisions.filter(
                revision_number=response['revision_number']).get()
        eq_(revision3.revision_number, 3)
        eq_(revision3.attachments.count(), 1)
        att = revision3.attachments.filter(filename=filename).get()
        eq_(att.read(), content2)
        response = self.client.get(reverse('jp_attachment', args=[att.pk]))
        eq_(response.content, content2)

    def test_attachment_extension_too_long(self):
        res = self.upload(self.get_upload_url(self.revision.revision_number), 'foo', 'file.toolongofanextension')
        eq_(res.status_code, 403)

    def test_attachment_filename_sanitization(self):

        revision = self.add_one(filename='My Photo of j0hnny.jpg')
        att = revision.attachments.all()[0]
        eq_(att.filename, 'My-Photo-of-j0hnny')
        revision.attachment_remove(att)

        revision = self.add_one(filename='^you*()"[]"are-_crazy')
        att = revision.attachments.all()[0]
        eq_(att.filename, '^you-()-are-_crazy')
        revision.attachment_remove(att)

        revision = self.add_one(filename='"><a href="">test')
        att = revision.attachments.all()[0]
        eq_(att.filename, '-a-href=-test')
        revision.attachment_remove(att)

        revision = self.add_one(filename='template.html.js')
        att = revision.attachments.all()[0]
        eq_(att.filename, 'template.html')
        revision.attachment_remove(att)

        revision = self.add_one(filename='image.-png^*(@&#')
        att = revision.attachments.all()[0]
        eq_(att.filename, 'image')
        eq_(att.ext, 'png')
        revision.attachment_remove(att)

        revision = self.add_one(filename='image.<a href=""')
        att = revision.attachments.all()[0]
        eq_(att.filename, 'image')
        eq_(att.ext, 'ahref')
        revision.attachment_remove(att)

        revision = self.add_one(filename='data+test.js')
        att = revision.attachments.all()[0]
        eq_(att.filename, 'data+test')
        revision.attachment_remove(att)

    def get_revision_from_response(self, response):
        return self.package.revisions.filter(
                revision_number=response['revision_number']).get()

    def create_next_attachment(self, revision, filename, ext='html'):
        response = simplejson.loads(
                self.client.post(
                    self.get_add_url(revision),{
                    "filename": "%s.%s" % (filename, ext)}
                    ).content)
        return response, self.get_revision_from_response(response)

    def test_attachment_delete_not_empty_directory(self):
        # creating first attachment (on the level of directory to be deleted)
        filename1 = "test1/test1"
        response = simplejson.loads(
                self.client.post(self.add_url, {
                    "filename": "%s.html" % filename1}).content)
        revision = self.package.revisions.filter(
                revision_number=response['revision_number']).get()
        # creating second attachment (outside of deleted directory)
        filename_other = "test2/test1"
        response, revision = self.create_next_attachment(revision,
                filename_other)
        # creating third attachment (one level deep inside directory to be del)
        filename2 = "test1/test2/test2"
        response, revision = self.create_next_attachment(revision,
                filename2)
        eq_(revision.attachments.count(), 3)
        response = simplejson.loads(
                self.client.post(revision.get_remove_folder_url(), {
                    'name': 'test1/', 'root_dir': 'data'}).content)
        revision = self.get_revision_from_response(response)
        eq_(revision.attachments.count(), 1)
        eq_(revision.folders.count(), 0)

    def test_private_attachments(self):
        revision = self.add_one()
        revision.package.active = False
        revision.package.save()

        att = revision.attachments.all()[0]
        url = reverse('jp_attachment', args=[att.get_uid])

        res = self.client.get(url)
        eq_(res.status_code, 200)

        self.client.logout()
        res = self.client.get(url)
        eq_(res.status_code, 403)

