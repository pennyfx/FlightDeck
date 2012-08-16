# coding=utf-8
import commonware
import tempfile
import os
import datetime
import decimal

from test_utils import TestCase

from nose.tools import eq_

from django.contrib.auth.models import User
from django.conf import settings

from jetpack.tasks import calculate_activity_rating
from jetpack.models import Package, PackageRevision, Module, Attachment, SDK
from jetpack.errors import SelfDependencyException, FilenameExistException, \
        DependencyException

from utils import validator

from base.helpers import hashtag

log = commonware.log.getLogger('f.test')


class PackageRevisionTest(TestCase):
    fixtures = ['mozilla_user', 'users', 'core_sdk', 'packages', 'old_packages']

    def setUp(self):
        self.author = User.objects.get(username='john')
        self.addon = self.author.packages_originated.addons()[0:1].get()
        self.library = self.author.packages_originated.libraries()[0:1].get()
        self.hashtag = hashtag()
        self.xpi_file = os.path.join(settings.XPI_TARGETDIR,
                "%s.xpi" % self.hashtag)

    def tearDown(self):
        if os.path.exists(self.xpi_file):
            os.remove(self.xpi_file)

    def test_first_revision_creation(self):
        addon = Package(author=self.author, type='a')
        addon.save()
        revisions = PackageRevision.objects.filter(package__pk=addon.pk)
        eq_(1, revisions.count())
        revision = revisions[0]
        eq_(revision.full_name, addon.full_name)
        eq_(revision.name, addon.name)
        eq_(revision.author.username, addon.author.username)
        eq_(revision.revision_number, 0)
        eq_(revision.pk, addon.latest.pk)
        eq_(revision.pk, addon.version.pk)
        eq_(revision.name, addon.name)

    def test_name_change(self):
        addon = Package(author=self.author, type='a')
        addon.save()
        revisionA = PackageRevision.objects.filter(package__pk=addon.pk)[0]
        addon.latest.set_full_name("TEST NAME CHANGE")
        addon.save()
        addon.latest.save()
        revisionB = PackageRevision.objects.filter(package__pk=addon.pk)[0]
        log.debug(revisionB.name)
        log.debug(addon.name)
        eq_(revisionB.name, addon.name)
        assert revisionA.pk != revisionB.pk
        assert revisionA.name != revisionB.name
        eq_(len(addon.revisions.all()), 2)

    def test_save(self):
        # system should create new revision on save
        addon = Package(author=self.author, type='a')
        addon.save()
        revisions = PackageRevision.objects.filter(package__name=addon.name)
        first = revisions[0]
        first.save()
        revisions = PackageRevision.objects.filter(package__name=addon.name)
        eq_(2, revisions.count())

        # first is not the same package anymore and it does not have
        # the version_name parameter
        eq_(None, first.version_name)

        # "old" addon doesn't know about the changes
        self.assertNotEqual(addon.latest.revision_number,
                            first.revision_number)

        # reloading addon to update changes
        addon = first.package

        # first is the latest
        eq_(addon.latest.revision_number,
                         first.revision_number)
        self.assertNotEqual(addon.version.revision_number,
                            addon.latest.revision_number)

    def test_set_version(self):
        addon = Package(author=self.author, type='a')
        addon.save()
        first = addon.latest
        old_id = first.id
        first.set_version('test')

        # setting version does not make new revision
        eq_(first.id, old_id)

        # setting version sets it for revision, package
        # and assigns revision to package
        eq_(first.version_name, 'test')
        eq_(first.package.version_name, 'test')
        eq_(first.package.version.pk, first.pk)

    def test_adding_and_removing_dependency(self):
        revisions = PackageRevision.objects.filter(package__pk=self.addon.pk)
        count = revisions.count()
        first = revisions[0]
        lib = PackageRevision.objects.filter(package__pk=self.library.pk)[0]

        # first depends on lib
        first.dependency_add(lib)
        revisions = PackageRevision.objects.filter(package__pk=self.addon.pk)

        # revisions number increased
        eq_(count + 1, revisions.count())

        first = revisions[1]
        second = revisions[0]

        # only the second revision has the dependencies
        eq_(0, first.dependencies.count())
        eq_(1, second.dependencies.count())

        # remove the dependency
        second.dependency_remove(lib)

        revisions = PackageRevision.objects.filter(package__pk=self.addon.pk)
        first = revisions[2]
        second = revisions[1]
        third = revisions[0]

        # only the second revision has the dependencies
        eq_(0, first.dependencies.count())
        eq_(1, second.dependencies.count())
        eq_(0, third.dependencies.count())

    def test_save_with_dependency(self):
        # system should copy on save with all dependencies
        revisions = PackageRevision.objects.filter(package__pk=self.addon.pk)
        first = revisions[0]
        lib = PackageRevision.objects.filter(package__pk=self.library.pk)[0]

        # make first depends on lib
        # it's setting dependency in django standard way, to keep
        # revision structure
        first.dependencies.add(lib)

        # save creates a new revision
        first.save()
        revisions = PackageRevision.objects.filter(package__pk=self.addon.pk)
        first = revisions[1]
        second = revisions[0]
        # both revisions have the same dependencies
        eq_(first.dependencies.count(),
                         second.dependencies.count())
        eq_(first.dependencies.all()[0].pk, lib.pk)
        eq_(second.dependencies.all()[0].pk, lib.pk)

    def test_adding_addon_as_dependency(self):
        " Add-on can't be a dependency "
        lib = PackageRevision.objects.filter(package__pk=self.library.pk)[0]
        addon = PackageRevision.objects.filter(package__pk=self.addon.pk)[0]
        self.assertRaises(TypeError, lib.dependency_add, addon)
        eq_(0, lib.dependencies.all().count())

    def test_adding_library_twice(self):
        " Check recurrent dependency (one level deep) "
        lib = self.library.latest
        addon = self.addon.latest
        addon.dependency_add(lib)
        self.assertRaises(DependencyException, addon.dependency_add, lib)


    def test_adding_library_naming_conflict(self):
        " Check recurrent dependency (all levels) "
        john_lib = self.library
        john_lib2 = Package(author=self.author, type='l')
        john_lib2.save()

        jan = User.objects.get(username='jan')
        jan_lib = Package(author=jan, type='l')
        jan_lib.save()
        jan_conflict = Package(author=jan, type='l', full_name=john_lib.full_name)
        jan_conflict.save()

        john_lib.latest.dependency_add(jan_lib.latest)

        addon = self.addon.latest
        addon.dependency_add(john_lib.latest)
        addon.dependency_add(jan_lib.latest)

        self.assertRaises(DependencyException, addon.dependency_add, jan_conflict.latest)


        john_lib2.latest.dependency_add(jan_conflict.latest)
        self.assertRaises(DependencyException, addon.dependency_add, john_lib2.latest)

    def test_adding_libs_with_common_dependency(self):
        # if B and C both depend on D, at the same version, then it should
        # be able to add both to A
        addon = Package(author=self.author, type='a')
        addon.save()
        lib1 = Package(author=self.author, type='l')
        lib1.save()
        lib2 = Package(author=self.author, type='l')
        lib2.save()
        lib3 = Package(author=self.author, type='l')
        lib3.save()

        lib1.latest.dependency_add(lib3.latest)
        lib2.latest.dependency_add(lib3.latest)

        addon.latest.dependency_add(lib1.latest)
        addon.latest.dependency_add(lib2.latest)

        assert True

    def test_adding_library_self(self):
        " Check recurrent dependency (one level only) "
        lib = self.library.latest
        self.assertRaises(SelfDependencyException, lib.dependency_add, lib)

    def test_removing_not_existing_dependency(self):
        " Removing not existing dependency should raise an error "
        self.assertRaises(DependencyException,
                          self.addon.latest.dependency_remove_by_id_number,
                          self.library.id_number)
        self.assertRaises(DependencyException,
                         self.addon.latest.dependency_remove,
                         self.library.latest)

    def test_adding_module(self):
        " Test if module is added properly "
        addon = Package(author=self.author, type='a')
        addon.save()
        first = addon.latest
        # add module
        first.module_create(
            filename='test',
            author=self.author
        )

        " module should be added to the latter only "
        revisions = addon.revisions.all()
        first = revisions[1]
        second = revisions[0]

        # all add-ons have a default modules created
        eq_(1, first.modules.count())
        eq_(2, second.modules.count())

    def test_adding_attachment(self):
        " Test if attachment is added properly "
        addon = Package(author=self.author, type='a')
        addon.save()
        first = addon.latest
        first.attachment_create(
            filename='test.txt',
            author=self.author
        )

        " module should be added to the latter revision only "
        revisions = addon.revisions.all()
        first = revisions[1]
        second = revisions[0]

        eq_(0, first.attachments.count())
        eq_(1, second.attachments.count())

    def test_read_write_attachment(self):
        """Test that we can read and write to an attachment."""
        addon = Package(author=self.author, type='a')
        addon.save()
        first = addon.latest
        filename = tempfile.mkstemp()[1]
        try:
            attachment = first.attachment_create(
                filename='test',
                ext='txt',
                author=self.author
            )
            attachment.data = 'This is a test.'
            attachment.write()
            assert attachment.read() == attachment.data
            assert not attachment.changed()
        finally:
            os.remove(filename)

    def test_updating_module(self):
        " Updating module has some additional action "
        addon = Package(author=self.author, type='a')
        addon.save()
        first = addon.latest
        mod = first.module_create(
            filename='test_filename',
            author=self.author
        )
        mod.code = 'test'
        first.update(mod)

        # create new revision on module update
        eq_(3, addon.revisions.count())
        eq_(2, Module.objects.filter(
            filename='test_filename').count())

        first = addon.revisions.all()[1]
        last = addon.revisions.all()[0]

        eq_(2, last.modules.count())

    def test_adding_module_with_existing_filename(self):
        " filename is unique in package "
        first = PackageRevision.objects.filter(package__pk=self.addon.pk)[0]
        first.module_create(
            filename='test_filename',
            author=self.author
        )
        # Exception on creating the module from PackageRevision
        self.assertRaises(FilenameExistException, first.module_create,
            **{'filename': 'test_filename', 'author': self.author}
        )
        # Exception on adding a different module with the same filename
        mod = Module.objects.create(
            filename='test_filename',
            author=self.author
        )
        self.assertRaises(FilenameExistException, first.module_add, mod)

    def test_adding_attachment_with_existing_filename(self):
        """Filename is unique per packagerevision."""
        first = PackageRevision.objects.filter(package__pk=self.addon.pk)[0]
        first.attachment_create(
            filename='test_filename',
            ext='txt',
            author=self.author
        )
        self.assertRaises(FilenameExistException, first.attachment_create,
            **{'filename': 'test_filename',
               'ext': 'txt',
               'author': self.author}
        )
        att = Attachment.objects.create(
            filename='test_filename',
            ext='txt',
            author=self.author
        )
        self.assertRaises(FilenameExistException, first.attachment_add, att)

    def test_adding_extra_package_properties(self):
        addon = Package(type='a', author=self.author)
        addon.save()
        pk = addon.pk
        rev = addon.latest

        rev.set_extra_json('''
        {
            "preferences": [{
                "name": "example",
                "type": "string",
                "title": "foo",
                "value": "bar"
            }],
            "id": "baz"
        }
        ''')

        addon = Package.objects.get(pk=pk) # breaking cache
        manifest = addon.latest.get_manifest()
        assert 'preferences' in manifest
        eq_(manifest['preferences'][0]['name'], 'example')

        # user-provide values don't override our generated ones
        self.assertNotEqual(manifest['id'], 'baz')

        assert 'Extra JSON' in addon.latest.commit_message

    def test_adding_invalid_extra_json(self):
        addon = Package(type='a', author=self.author)
        addon.save()
        pk = addon.pk
        rev = addon.latest

        from simplejson import JSONDecodeError
        self.assertRaises(JSONDecodeError, rev.set_extra_json, '''
        {
            foo: baz
        }
        ''')

        from jetpack.errors import IllegalFilenameException
        self.assertRaises(IllegalFilenameException, rev.set_extra_json, '''
        {
            "icon": "/user/sean/.ssh/config"
        }
        ''')

    def test_add_commit_message(self):
        author = User.objects.all()[0]
        addon = Package(type='a', author=author)
        addon.save()
        rev = addon.latest
        rev.add_commit_message('one')
        rev.add_commit_message('two')
        rev.save()

        eq_(rev.commit_message, 'one, two')

        # revision has been saved, so we should be building up a new commit message
        rev.add_commit_message('three')
        rev.save()
        eq_(rev.commit_message, 'three')


    def test_force_sdk(self):
        addon = Package.objects.create(
            full_name="Other Package",
            author=self.author,
            type='a')
        oldsdk = addon.latest.sdk

        mozuser = User.objects.get(username='4757633')
        version='testsdk'
        kit_lib = PackageRevision.objects.create(
                author=mozuser,
                package=oldsdk.kit_lib.package,
                revision_number=oldsdk.kit_lib.revision_number + 1,
                version_name=version)
        core_lib = PackageRevision.objects.create(
                author=mozuser,
                package=oldsdk.core_lib.package,
                revision_number=oldsdk.core_lib.revision_number + 1,
                version_name=version)
        sdk = SDK.objects.create(
                version=version,
                kit_lib=kit_lib,
                core_lib=core_lib,
                dir='somefakedir')

        addon.latest.force_sdk(sdk)
        eq_(len(addon.revisions.all()), 1)
        eq_(addon.latest.sdk.version, version)
        eq_(addon.latest.commit_message,
                'Automatic Add-on SDK upgrade to version (%s)' % sdk.version)
        addon.latest.force_sdk(oldsdk)
        eq_(len(addon.revisions.all()), 1)
        eq_(addon.latest.commit_message.count('SDK'), 2)

    def test_fix_old_packages(self):
        old_rev = PackageRevision.objects.get(pk=401)
        assert not old_rev.full_name
        old_rev.force_sdk(self.addon.latest.sdk)
        old_package = Package.objects.get(pk=401)
        assert old_rev.full_name
        assert old_rev.name
        assert old_package.full_name
        assert old_package.name

    def test_update_package_activity_cron(self):
        addon = Package(type='a', author=self.author)
        addon.save()

        now = datetime.datetime.now()

        # Create 1 weeks worth of revisions... should equal .30 of score
        # see models.py def Packages for weights

        for i in range(1,8):
            r = addon.revisions.create(author=self.author, revision_number=i)
            r.created_at=now-datetime.timedelta(i)
            super(PackageRevision, r).save()

        #run task on this one package
        calculate_activity_rating([addon.pk])

        addon = Package.objects.get(pk=addon.pk)

        eq_(addon.activity_rating, addon.calc_activity_rating())

    def test_commit_message_utf8(self):
        self.addon.latest.message = u'some ut8 - ą'
        self.addon.latest.save();
        rev = self.addon.revisions.latest('pk')
        eq_(rev.message, self.addon.latest.message)

    def test_cached_hashtag(self):
        assert validator.is_valid('alphanum',
                self.addon.latest.get_cache_hashtag())

    """
    Althought not supported on view and front-end,
    there is no harm in these two

    def test_adding_module_which_was_added_to_other_package_before(self):
        " ""
        system should prevent from adding a module to more than one packages
        " ""
        addon = Package.objects.create(
            full_name="Other Package",
            author=self.author,
            type='a'
        )
        rev = addon.latest
        first = PackageRevision.objects.filter(package__pk=self.addon.pk)[0]
        mod = Module.objects.create(
            filename='test_filename',
            author=self.author
        )
        first.module_add(mod)
        self.assertRaises(AddingModuleDenied, rev.module_add, mod)

    def test_adding_attachment_which_was_added_to_other_package_before(self):
        " assigning attachment to more than one packages should be prevented! "
        addon = Package.objects.create(
            full_name="Other Package",
            author=self.author,
            type='a'
        )
        rev = addon.latest
        first = PackageRevision.objects.filter(package__pk=self.addon.pk)[0]
        att = Attachment.objects.create(
            filename='test_filename',
            ext='.txt',
            path='/tmp/upload_path',
            author=self.author
        )
        first.attachment_add(att)
        self.assertRaises(AddingAttachmentDenied, rev.attachment_add, att)
    """

