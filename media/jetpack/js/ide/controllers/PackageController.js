var Class = require('shipyard/class/Class'),
    Options = require('shipyard/class/Options'),
    Events = require('shipyard/class/Events'),
    dom = require('shipyard/dom'),
    object = require('shipyard/utils/object'),
    string = require('shipyard/utils/string'),
    log = require('shipyard/utils/log'),
    Cookie = require('shipyard/utils/Cookie'),
    Request = require('shipyard/http/Request'),
    
    Module = require('../models/Module'),
    Attachment = require('../models/Attachment'),
    Folder = require('../models/Folder'),
    Package = require('../models/Package'),
    PackageRevision = require('../models/PackageRevision'),

    filename = require('../utils/filename'),

    FloatingTips = require('../views/FloatingTips'),
    Validator = require('../views/Validator'),
	JSONValidator = require('../views/JSONValidator'),
    
    //TODO: this is bad practice
    settings = dom.window.get('settings');

function fd() {
    return dom.window.get('fd');
}

var LOADING_CLASS = 'loading';

module.exports = new Class({

    //Extends: Controller?,

    Implements: [Events, Options],

    options: {
        modules: [],
        attachments: [],
        dependencies: [],
        folders: [],
        readonly: false,
        package_info_el: 'package-properties',
        copy_el: 'package-copy',
        test_el: 'try_in_browser',
        download_el: 'download',
        console_el: 'error-console',
        save_el: 'package-save',
        menu_el: 'UI_Editor_Menu',

        package_info_form_elements: [
			'full_name',
			'package_description',
			'package_extra_json',
      'jid'
		],
        
        check_dependencies: true,
        check_if_latest: true  // switch to false if displaying revisions

    },
    
    package_: null,

    //junk
    data: {},

    modules: {},
    attachments: {},
    folders: {},
    dependencies: {},

    edited: 0,

    initialize: function PackageController(package_, options, editor, tabs, sidebar) {
        this.package_ = package_;
        this.editor = editor;
        this.tabs = tabs;
        this.sidebar = sidebar;
        this.setOptions(options);

                

        this.instantiate_modules();
        this.instantiate_attachments();
        this.instantiate_folders();
        this.instantiate_dependencies();
        
        this.assignActions();

        this.setupButtonTooltips();
    },

    assignActions: function assignActions() {
        var controller = this,
            package_ = this.package_;
        
        // All actions

        // reset version_name (in case of reload)
        var versionEl = this.versionEl = dom.$('version_name');
        if (versionEl) {
            versionEl.set('value', package_.get('version_name'));
            package_.observe('version_name', function(name) {
                versionEl.set('value', name);
            });
        }

        var packageInfoNameEl = this.packageInfoNameEl = dom.$('package-info-name');
        if (packageInfoNameEl) {
            package_.observe('full_name', function(name) {
                packageInfoNameEl.set('text', name);
            });
        }

        var packageNameEl = this.packageNameEl = dom.$('package-info-name');
        if (packageNameEl) {
            package_.observe('full_name', function(name) {
                packageNameEl.set('text', name);
            });
        }

        this.revision_list_btn = dom.$('revisions_list');
        this.revision_list_btn.addListener('click', function(e) {
            e.preventDefault();
            controller.showRevisionList();
        });
        if (this.package_.isAddon()) {
            this.boundTestAddon = this.testAddon.bind(this);
            this.test_el = dom.$(this.options.test_el);
            this.options.test_url = this.test_el.getElement('a').get('href');
            this.test_el.addListener('click', function(e) {
                e.preventDefault();
                controller.testAddon();
            });

            this.download_el = dom.$(this.options.download_el);
            this.options.download_url = this.download_el.getElement('a').get('href');
            this.download_el.addListener('click', function(e) {
                e.preventDefault();
                if (this.hasClass(LOADING_CLASS)) {
                    return;
                }
                controller.downloadAddon();
            });
        }
        this.copy_el = dom.$(this.options.copy_el);
        if (this.copy_el) {
            this.copy_el.addListener('click', function(e) {
                e.preventDefault();
                if (this.hasClass(LOADING_CLASS)) {
                    return;
                }
                controller.copyPackage();
            });
        }
        if (this.options.check_if_latest) {
            dom.window.addListener('focus', function() {
                controller.checkIfLatest(controller.askForReload);
            });
        }

        fd().addListener('xpi_downloaded', function() {
            controller.generateHashtag();
        });

        this.packageInfoEl = dom.$(this.options.package_info_el);

        this.attachEditor();
        this.attachSidebar();
        this.attachTabs();
        this.bind_keyboard();

        if (this.getOption('readonly')) {
            this.assignViewActions();
        } else {
            this.assignEditActions();
        }
    },

    assignViewActions: function() {
        var controller = this;

        this.packageInfoEl.addListener('click', function(e) {
            e.preventDefault();
            controller.showInfo();
        });
    },

    assignEditActions: function assignEditActions() {
        var controller = this;

        dom.window.addListener('beforeunload', function(e) {
            controller.alertUnsavedData(e);
        });

        this.packageInfoEl.addListener('click', function(e) {
            e.preventDefault();
            controller.editInfo();
        });

        if (this.package_.isAddon()) {
            this.console_el = dom.$(this.options.console_el);
            this.console_el.addListener('click', function(e) {
                e.preventDefault();
                var abh = dom.window.get('mozFlightDeck');
                if (abh) {
                    abh.send('toggleConsole', 'open');
                } else {
                    log.warn('No mozFlightDeck.');
                }
            });
        }

        this.save_el = dom.$(this.options.save_el);
        this.save_el.addListener('click', function(e) {
            e.preventDefault();
            controller.saveAction();
        });
        
        this.setupSaveOptions();
        // when typing in Save popover, you should be able to tab in a
        // logical order
        this.save_el.addListener('mouseenter', function(e) {
            controller.versionEl.focus();
        });
        this.revision_message_el = dom.$('revision_message');
        this.revision_message_el.addListener('keypress', function(e) {
            if (e.key === 'tab') {
                e.preventDefault();
                controller.save_el.focus();
            }
        });

        if (this.getOptions('check_dependencies')) {
            this.prepareDependenciesInterval();
        }

        this.sdkVersionEl = dom.$('jetpack_core_sdk_version');
        if (this.sdkVersionEl) {
            this.sdkVersionEl.addListener('change', function() {
                var loader = dom.$('core_library_lib');
                loader.addClass(LOADING_CLASS).addClass('small');
                new Request({
                    url: controller.options.switch_sdk_url,
                    data: {
                        'id': controller.sdkVersionEl.get('value')
                    },
                    onSuccess: function(text) {
                        var response = JSON.parse(text);
                        // set the redirect data to view_url of the new revision
                        //fd().setURIRedirect(response.view_url);
                        // set data changed by save
                        controller.registerRevision(response);
                        // change url to the SDK lib code
                        dom.$('core_library_lib').getElement('a').set(
                            'href', response.lib_url);
                        // change name of the SDK lib
                        dom.$('core_library_lib').getElement('span').set(
                            'text', response.lib_name);
                        fd().message.alert(response.message_title, response.message);
                    },
                    onComplete: function() {
                        loader.removeClass(LOADING_CLASS);
                    }
                }).send();
            });
        }
    },

    setupSaveOptions: function() {
        // when typing in Save popover, you should be able to tab in a
        // logical order
        var saveOptionsEl = dom.$('package-save-options');
        var controller = this;

        saveOptionsEl.addListener('click', function(e) {
            e.preventDefault();
            var li = this.getParent('li');
            if (li.hasClass('open')) {
                li.removeClass('open');
            } else {
                li.addClass('open');
                controller.versionEl.focus();
            }
        });
        this.revision_message_el = dom.$('revision_message');
        this.revision_message_el.addListener('keypress', function(e) {
            if (e.key === 'tab') {
                e.preventDefault();
                controller.save_el.focus();
            }
        });

        this.addListener('saving', function() {
            saveOptionsEl.getParent('li').removeClass('open');
        });
    },

    instantiate_modules: function() {
        // iterate by modules and instantiate Module
        var mainLoaded = false,
            firstModule;
        this.options.modules.forEach(function(module) {
            module.readonly = this.options.readonly;
            var mod = this.newModule(module);
            if (module.main) {
                mainLoaded = true;
                this.editFile(mod);
            }
            if (!firstModule) {
                firstModule = mod;
            }
        }, this);

        if (!mainLoaded && firstModule) {
            this.editFile(firstModule);
        }
    },

    instantiate_attachments: function() {
        // iterate through attachments
        this.options.attachments.forEach(function(attachment) {
            attachment.readonly = this.options.readonly;
            this.newAttachment(attachment);
        }, this);
    },

    instantiate_dependencies: function() {
        // iterate through attachments
        this.options.dependencies.forEach(function(plugin) {
            this.newDependency(plugin);
        }, this);
    },
    
    instantiate_folders: function() {
        this.options.folders.forEach(function(folder) {
            this.newFolder(folder);
        }, this);
    },

    newModule: function(data) {
        var mod = new Module(data);
        this.modules[mod.get('uid')] = mod;
        this.sidebar.addLib(mod);
        this.editor.registerItem(mod.get('uid'), mod);
        var controller = this;
        mod.addListener('destroy', function() {
            delete controller.modules[this.get('uid')];
        });
        return mod;
    },

    newAttachment: function(data) {
        if (!data.id && !data.pk) {
            data.pk = data.uid;
        }
        var att = new Attachment(data),
            controller = this;
        this.attachments[att.get('uid')] = att;
        att.observe('uid', function(updated, old) {
            controller.attachments[updated] = att;
            delete controller.attachments[old];
        });

        if (this.sidebar) {
            // if check is for tests
            this.sidebar.addData(att);
        }
        if (this.editor) {
            // if check is for tests
            this.editor.registerItem(att.get('uid'), att);
        }

        att.addListener('destroy', function() {
            delete controller.attachments[this.get('uid')];
        });
        return att;
    },

    newFolder: function(data) {
        var folder = new Folder(data);
        this.folders[folder.get('uid')] = folder;
        var rootDir = folder.get('root_dir');

        if (rootDir === Folder.ROOT_DIR_LIB) {
            this.sidebar.addLib(folder);
        } else if (rootDir === Folder.ROOT_DIR_DATA) {
            this.sidebar.addData(folder);
        }
        
        var controller = this;
        folder.addListener('destroy', function() {
            delete controller.folders[this.get('uid')];
        });
    },

    newDependency: function(data) {
        var lib = new Package(data);
        this.dependencies[lib.get('uid')] = lib;
        this.sidebar.addPlugin(lib);

        var controller = this;
        lib.addListener('destroy', function() {
            delete controller.dependencies[this.get('uid')];
        });
    },

    showRevisionList: function() {
        var loader = this.revision_list_btn.getElement('a');
        loader.addClass(LOADING_CLASS).addClass('small');
        new Request({
            method: 'get',
            url: string.substitute(this.options.revisions_list_html_url, this.options),
            onSuccess: function(html) {
                var modal = fd().displayModal(html),
                    modalEl = dom.$(modal).getElement('.UI_Modal'),
                    showVersionsEl = modalEl.getElement('#versions_only');
                //setup handler for "Show versions only" checkbox
                function toggleVersionsOnly() {
                    if (showVersionsEl.get('checked')) {
                        modalEl.addClass('boolean-on');
                    } else {
                        modalEl.removeClass('boolean-on');
                    }
                }
                showVersionsEl.addListener('change', function(e) {
                    toggleVersionsOnly();
                });
                toggleVersionsOnly();
            },
            onComplete: function() {
                loader.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    setupButtonTooltips: function() {
        this.tips = new FloatingTips('.UI_Editor_Menu .UI_Editor_Menu_Button', {
            position: 'top',
            balloon: true
        });
    },

    checkIfLatest: function(failCallback) {
        // we want to make a request each time, since the author could
        // have created new changes while we were looking.
        var controller = this;

        /*PackageRevision.find({
            conditions: { package: this.package_.get('pk') },
            options: { limit: 1, order_by: '-revision_number' },
            callback: function(r) {
                r = r[0];
                if (r.get('revision_number') > controller.package.get('revision_number')) {
                    failCallback.call(controller);
                }
            }
        })*/

        // ask backend for the latest revision number
        new Request({
            method: 'get',
            url: this.options.check_latest_url,
            onSuccess: function(text) {
                var response = JSON.parse(text);
                if (failCallback && controller.package_.get('revision_number') < response.revision_number) {
                    failCallback.call(this);
                }
            }.bind(this)
        }).send();

    },

    askForReload: function() {
        fd().warning.alert(
            'New revision detected',
            'There is a newer revision available. <a href="'+
            this.options.latest_url +'">Click this link to go to it now.</a>'
        );
    },

    /*
     * Method: copyPackage
     * create a new Package with the same name for the current user
     */
    copyPackage: function() {
        if (this._is_copying) {
            return;
        }

        if (!settings.user) {
            fd().alertNotAuthenticated();
            return;
        }
        
        if (this.edited) {
            fd().error.alert("There are unsaved changes",
                    "To make a copy, please save your changes.");
            return;
        }

        this._is_copying = true;

        var controller = this;
        var loader = this.copy_el.getElement('a');
        loader.addClass(LOADING_CLASS).addClass('small');
        new Request({
            url: this.options.copy_url,
            method: 'post',
            onSuccess: function(text) {
                var response = JSON.parse(text);
                dom.window.set('location', response.view_url);
            },
            onComplete: function() {
                controller._is_copying = false;
                loader.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    downloadAddon: function() {
        var el = dom.$(this.options.download_el).getElement('a');
        if (el.hasClass('clicked')) {
            return;
        }
        el.addClass('clicked');

        fd().tests[this.options.hashtag] = {
            spinner: el.addClass('loading').addClass('small')
        };
        var data = {
            hashtag: this.options.hashtag,
            filename: this.package_.get('name')
        };
        new Request({
            url: this.options.download_url,
            method: 'post',
            data: data,
            onComplete: function() {
                el.removeClass('clicked');
                // remove spinner
                el.removeClass('loading').removeClass('small');
            },
            onSuccess: function() {
                fd().downloadXPI(data);
            }
        }).send();
    },

    testAddon: function(){
        if (!this.getOption('readonly')) {
            this.collectData();
            this.data.live_data_testing = true;
        }
        var el = this.test_el;
        if (fd().alertIfNoAddOn()) {
            if (el.hasClass('pressed')) {
                fd().uninstallXPI(el.get('data-jetpackid'));
            } else {
                this.installAddon();
            }
        } else {
            fd().whenAddonInstalled(function() {
                fd().message.alert(
                    'Add-on Builder Helper',
                    'Now that you have installed the Add-on Builder Helper, loading the add-on into your browser for testing...'
                );
                this.testAddon();
            }.bind(this));
        }
    },

    installAddon: function() {
        if (this._test_request && this._test_request.isRunning()) {
            log.debug('Test request already running. Cancelling second attempt');
            return;
        }
        
        var loader = this.test_el.getElement('a');
        fd().tests[this.options.hashtag] = {
            spinner: loader.addClass(LOADING_CLASS).addClass('small')
        };
        var data = this.data || {};
        data.hashtag = this.options.hashtag;
        this._test_request = new Request({
            url: this.options.test_url,
            data: data,
            onSuccess: function() {
                fd().testXPI(data);
            },
            onFailure: function() {
                log.error('Failed to start testing addon');
                loader.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    generateHashtag: function() {
        if (this.getOption('readonly')) {
            return;
        }
        this.options.hashtag = fd().generateHashtag(this.package_.get('id_number'));
    },

    //Package.View
    showInfo: function() {
        fd().displayModal(this.options.package_info);
    },

    //Package.Edit
    attachEditor: function() {
        var controller = this;
        if(!this.editor) {
            return;
        }

        this.editor.addListener('change', function() {
            controller.onChanged();
        });
        
        this.addListener('change', this.onChanged);
        this.addListener('save', this.onSaved);
        this.addListener('reset', this.onReset);
    },

    attachSidebar: function() {
        if (!this.sidebar) {
            return;
        }

        var controller = this;
        this.sidebar.addListener('select', function(file) {
            controller.onSidebarSelect(file);
        });
    },

    attachTabs: function() {
        if (!this.tabs) {
            return;
        }

        var controller = this;
        this.tabs.addListener('select', function(tab) {
            controller.onTabSelect(tab);
        });
    },

    onSidebarSelect: function(file) {
        // if file is Module or Editable Attachment
        if ((file instanceof Module) ||
            (file instanceof Attachment && file.isEditable())) {
            // then open tab and editor
            this.editFile(file);
        }
        // else if uneditable Attachment
        else if (file instanceof Attachment) {
            // then show in fd().modal
            this.showAttachmentModal(file);
        }
        // else if Library
        else if (file instanceof Package) {
            // then open link in new window
            dom.window.getNode().open(file.get('view_url'));
        }
    },

    onTabSelect: function(tab) {
        this.sidebar.selectFile(tab.file);
        this.editFile(tab.file);
    },

    editFile: function(file) {
        this.tabs.selectTab(file);
        this.editor.switchTo(file.get('uid')).focus();
    },

    showAttachmentModal: function(file) {
        var template_start = '<div id="attachment_view"><h3>'+
            file.get('shortName')+'</h3><div class="UI_Modal_Section">';
        var template_end = '</div><div class="UI_Modal_Actions"><ul><li>'+
            '<input type="reset" value="Close" class="closeModal"/>'+
            '</li></ul></div></div>';
        var template_middle = 'Download <a href="'+
            file.get('get_url')+
            '">'+
            file.get('shortName')+
            '</a>';
        var spinner, img, modal, target;
        if (file.isImage()) {
            template_middle += '<p></p>';
            img = new dom.Element('img', { src: file.get('get_url') });
            img.addListener('load', function() {
                if (target) {
                    target.removeClass(LOADING_CLASS);
                }
                modal._position();
            });
        }
        modal = fd().displayModal(template_start+template_middle+template_end);
        target = dom.$(modal).getElement('.UI_Modal_Section p');
        if (target && img) {
            target.addClass(LOADING_CLASS);
            img.inject(target);
        }
        setTimeout(function() {
            modal._position();
        }, 1);
    },
                      
    onChanged: function() {
        log.info('document changed - onbeforeunload warning is on and save button is lit.');
        dom.$$('li.Icon_save').addClass('Icon_save_changes');
        this.edited++;
    },

    onSaved: function() {
        //any save specific logic?
        this.emit('reset');
    },
    
    onReset: function() {
        log.info('document saved - onbeforeunload warning is off and save button is not lit.');
        dom.$$('li.Icon_save').removeClass('Icon_save_changes');
        this.edited = 0;
    },

    downloadAddonOrSave: function(e){
        if (e) {
          e.preventDefault();
        }
        var that = this;
        if (this.edited) {
            // display message
            fd().showQuestion({
                title: 'You\'ve got unsaved changes.',
                message: 'Choose from the following options',
                buttons: [{
                    text: 'Cancel',
                    type: 'reset',
                    'class': 'close'
                },{
                    text: 'Download without saving',
                    id: 'downloadwithoutsaving',
                    'class': 'submit',
                    type: 'button',
                    callback: function(){
                        this.downloadAddon();
                    }.bind(this)
                },{
                    text: 'Save &amp; Download',
                    id: 'saveanddonload',
                    'class': 'submit',
                    type: 'button',
                    callback: function(){
                        fd().once('save', this.boundDownloadAddon);
                        this.save();
                    }.bind(this),
                    'default': true
                }]
            });
        } else {
            this.downloadAddon(e);
        }
    },
    
    uploadAttachment: function(files, renameAfterLoad) {
        var controller = this;
        //var spinner = new Spinner($('attachments')).show();
        var file = files[0];
        
        var req = new Request({
            method: 'post',
            url: this.getOption('upload_attachment_url'),
            data: {
                'upload_attachment': file
            },
            headers: {
                'X-File-Name': file.name,
                'X-File-Size': file.fileSize,
                'X-CSRFToken': Cookie.read('csrftoken')
            },
            onComplete: function() {
          //      if (spinner) {
            //        spinner.destroy();
              //  }
            },
            onSuccess: function(text) {
                var response;
                try {
                    response = JSON.parse(text);
                } catch(ex) {
                    log.error('Decode error', ex);
                    return;
                }

                fd().message.alert(response.message_title, response.message);
                var attachment = controller.newAttachment({
                    filename: response.filename,
                    ext: response.ext,
                    author: response.author,
                    content: response.code,
                    get_url: response.get_url,
                    id: response.uid
                });
                controller.registerRevision(response);
                log.debug('all files uploaded');
                if (typeof renameAfterLoad === 'function') {
                    renameAfterLoad(attachment);
                }
            },
            onFailure: function(text) {
                fd().error.alert(
                    string.substitute('Error {status}', this.xhr),
                    string.substitute('{statusText}<br/>{responseText}', this.xhr)
                );
            }
        });
        
        log.debug('uploading ' + file.name);
        return req.send();
    },

    addExternalAttachment: function(url, filename) {
        // download content and create new attachment
        log.debug('downloading ' + filename + ' from ' + url);
        this.addNewAttachment(
            this.options.add_attachment_url,
            {url: url, filename: filename});
    },
    
    addAttachment: function(filename) {
        // add empty attachment
        this.addNewAttachment(
            this.options.add_attachment_url,
            {filename: filename});
    },

    addNewAttachment: function(url, data) {
        var controller = this;
        var loader = dom.$('attachments');
        loader.addClass(LOADING_CLASS);
        new Request({
            url: url,
            data: data,
            method: 'post',
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                var att = controller.newAttachment({
                    filename: response.filename,
                    ext: response.ext,
                    author: response.author,
                    content: response.code,
                    get_url: response.get_url,
                    pk: response.uid
                });
                if (att.isEditable()) {
                    controller.editFile(att);
                }
            },
            onComplete: function() {
                loader.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    renameAttachment: function(uid, newName, quiet) {
        var that = this,
            att = this.attachments[uid];
        
        // break off an extension from the filename
        var ext = filename.extname(newName) || '';
        if (ext) {
            newName = filename.basename(newName);
        }

        var attachmentEl = this.sidebar.getBranchFromPath(newName, 'data') ||
            this.sidebar.getBranchFromFile(att);

        var spinnerEl = attachmentEl || dom.$(this.sidebar.trees.data);
        spinnerEl.addClass(LOADING_CLASS).addClass('small');

        new Request({
            url: that.options.rename_attachment_url,
            data: {
                uid: uid,
                new_filename: newName,
                new_ext: ext
            },
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                that.registerRevision(response);
                if (!quiet) {
                    fd().message.alert(response.message_title, response.message);
                }
                
                if (!att) {
                    log.warn("Attachment (" + uid + ") couldn't be found in fd().item");
                    return;
                }
                att.set({
                    filename: response.filename,
                    ext: response.ext,
                    author: response.author,
                    content: response.code,
                    get_url: response.get_url,
                    uid: response.uid
                });
                if (!attachmentEl) {
                    that.sidebar.addData(att);
                }
                
            },
            onComplete: function() {
                spinnerEl.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    removeAttachment: function(attachment) {
        var controller = this;
        var loader = this.sidebar.getBranchFromFile(attachment);
        loader.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.remove_attachment_url,
            data: {uid: attachment.get('uid')},
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                attachment.destroy();
            }
        }).send();
    },
    
    addModule: function addModule(filename) {
        var controller = this;
        var loader = dom.$('modules');
        loader.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.add_module_url,
            data: {'filename': filename},
            onSuccess: function addModule_onSuccess(text) {
                var response = JSON.parse(text);
                // set the redirect data to view_url of the new revision
                //fd().setURIRedirect(response.view_url);
                // set data changed by save
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                // initiate new Module
                var mod = controller.newModule({
                    active: true,
                    filename: response.filename,
                    author: response.author,
                    content: response.code,
                    get_url: response.get_url
                });
                controller.editFile(mod);
            },
            onComplete: function addModule_onComplete() {
                loader.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    renameModule: function(oldName, newName) {
        newName = newName.replace(/\..*$/, '');
        var controller = this;
        var el = this.sidebar.getBranchFromPath(newName+'.js', 'lib');
        el.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.rename_module_url,
            method: 'post',
            data: {
                old_filename: oldName,
                new_filename: newName
            },
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                
                var mod = controller.modules[oldName];
                mod.set({
                    filename: response.filename
                });
                controller.modules[response.filename] = mod;
                // change the id of the element
                delete controller.modules[oldName];
            },
            onComplete: function() {
                el.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    removeModule: function(module) {
        var controller = this;
        var el = this.sidebar.getBranchFromFile(module);
        el.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.remove_module_url,
            method: 'post',
            data: module.toJSON(),
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                module.destroy();
            },
            onComplete: function() {
                el.removeClass(LOADING_CLASS);
            }
        }).send();
    },
    
    removeAttachments: function(path) {
        var el = this.sidebar.getBranchFromPath(path, 'data'),
            controller = this;
        el.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.remove_folder_url,
            data: {
                name: path,
                root_dir: 'data'
            },
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                response.removed_attachments.forEach(function(uid) {
                    controller.attachments[uid].destroy();
                });
                response.removed_dirs.forEach(function(name) {
                    controller.sidebar.removeFile(name, 'd');
                });
                controller.sidebar.removeFile(response.foldername, 'd');
            },
            onComplete: function() {
                el.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    removeModules: function(path) {
        var el = this.sidebar.getBranchFromPath(path, 'lib'),
            controller = this;
        el.addClass(LOADING_CLASS).addClass('small');
        new Request({
            url: this.options.remove_module_url,
            data: {filename: path+'/'},
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                response.removed_modules.forEach(function(filename) {
                    controller.modules[filename].destroy();
                });
                response.removed_dirs.forEach(function(name) {
                    controller.sidebar.removeFile(name, 'l');
                });
                
            },
            onComplete: function() {
                el.removeClass(LOADING_CLASS);
            }
        }).send();
    },
    
    addFolder: function addFolder(name, root_dir) {
        var controller = this;
        var el = root_dir === Folder.ROOT_DIR_LIB ?
            'modules' : 'attachments';
        el = dom.$(el);
        el.addClass(LOADING_CLASS).addClass('small');
        return new Request({ url: this.options.add_folder_url,
            data: {
                name: name,
                root_dir: root_dir
            },
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                controller.newFolder({
                    name: response.name,
                    root_dir: root_dir
                });
            },
            onComplete: function() {
                el.removeClass(LOADING_CLASS);
            }
        }).send();
    },
    
    removeFolder: function(folder) {
        var controller = this,
            el = this.sidebar.getBranchFromFile(folder);
        el.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.remove_folder_url,
            data: folder.toJSON(),
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                folder.destroy();
            },
            onComplete: function() {
                el.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    assignLibrary: function(library_id) {
        if (library_id) {
            var controller = this,
                el = dom.$('libraries');
            el.addClass(LOADING_CLASS).addClass('small');
            return new Request({
                url: this.options.assign_library_url,
                data: {'id_number': library_id},
                onSuccess: function(text) {
                    var response = JSON.parse(text);
                    // set the redirect data to view_url of the new revision
                    //fd().setURIRedirect(response.view_url);
                    // set data changed by save
                    controller.registerRevision(response);
                    fd().message.alert(response.message_title, response.message);
                    controller.newDependency({
                        full_name: response.library_full_name,
                        id_number: response.library_id_number,
                        library_name: response.library_name,
                        view_url: response.library_url,
                        revision_number: response.library_revision_number
                    });
                },
                onComplete: function() {
                    el.removeClass(LOADING_CLASS);
                }
            }).send();
        } else {
            fd().error.alert('No such Library', 'Please choose a library from the list');
        }
    },
    
    updateLibrary: function(lib, callback) {
        var controller = this,
            el = dom.$('libraries');
        el.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.update_library_url,
            data: {
                'id_number': lib.get('id_number'),
                'revision': lib.retrieveNewVersion().revision
            },
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                lib.set({
                    view_url: response.library_url
                });

                if (typeof callback === 'function') {
                    callback(response);
                }
            },
            onComplete: function() {
                el.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    checkDependenciesVersions: function checkDependenciesVersions() {
        var controller = this;
        return new Request({
            method: 'get',
            url: this.options.latest_dependencies_url,
            onSuccess: function(text) {
                var versions = JSON.parse(text);
                versions.forEach(function(latest_revision) {
                    var lib = controller.dependencies[latest_revision.id_number];
                    if (!lib) {
                        return;
                    }
                    lib.storeNewVersion(latest_revision);
                    controller.sidebar.setPluginUpdate(lib);
                });
            }
        }).send();
    },
    
    prepareDependenciesInterval: function prepareDependenciesInterval() {
        var that = this;
        function setCheckInterval() {
            clearInterval(that.checkDependenciesInterval);
            that.checkDependenciesVersions();
            that.checkDependenciesInterval = setInterval(function() {
                that.checkDependenciesVersions();
            }, 1000 * 60);
        }
        
        function unsetCheckInterval() {
            clearInterval(that.checkDependenciesInterval);
        }
        
        dom.window.addListener('focus', setCheckInterval);
        dom.window.addListener('blur', unsetCheckInterval);
        setCheckInterval();
        
    },
    
    removeLibrary: function(lib) {
        var controller = this,
            loader = this.sidebar.getBranchFromFile(lib);
        loader.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.remove_library_url,
            data: {'id_number': lib.get('id_number')},
            onSuccess: function(text) {
                var response = JSON.parse(text);
                //fd().setURIRedirect(response.view_url);
                controller.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                lib.destroy();
            },
            onComplete: function() {
                loader.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    moduleExists: function(filename) {
        return object.some(this.modules, function(mod) {
            return mod.get('filename') === filename;
        });
    },

    attachmentExists: function(filename) {
        return object.some(this.attachments, function(att) {
            return att.get('fullName') === filename;
        });
    },

    folderExists: function(name, rootDir) {
        return object.some(this.folders, function(folder) {
            return (folder.get('root_dir') === rootDir &&
                    folder.get('name') === name);
        });
    },

    /*
     * Method: makePublic
     * activate a package
     */
    makePublic: function(e) {
        e.stop();
        var controller = this;

        this.savenow = false;
        var activateButton = dom.$('UI_ActivateLink');
        if (activateButton.getElement('a').hasClass('inactive')) {
            return false;
        }
        activateButton.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: activateButton.getElement('a').get('href'),
            onSuccess: function(text) {
                var response = JSON.parse(text);
                fd().message.alert(response.message_title, response.message);
                fd().emit('activate_' + response.package_type);
                activateButton.addClass('pressed').getElement('a').addClass('inactive');
                dom.$('UI_DisableLink').removeClass('pressed').getElement('a').removeClass('inactive');
                controller.package_.set('active', true);
            },
            onComplete: function() {
                activateButton.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    /*
     * Method: makePrivate
     * deactivate a package
     */
    makePrivate: function(e) {
        e.stop();
        var controller = this;
        this.savenow = false;
        var deactivateButton = dom.$('UI_DisableLink');
        if (deactivateButton.getElement('a').hasClass('inactive')) {
            return false;
        }
        deactivateButton.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: deactivateButton.getElement('a').get('href'),
            onSuccess: function(response) {
                response = JSON.parse(response);
                fd().message.alert(response.message_title, response.message);
                fd().emit('disable_' + response.package_type);
                deactivateButton.addClass('pressed').getElement('a').addClass('inactive');
                dom.$('UI_ActivateLink').removeClass('pressed').getElement('a').removeClass('inactive');
                controller.package_.set('active', false);
            },
            onComplete: function() {
                deactivateButton.removeClass(LOADING_CLASS);
            }
        }).send();
    },

    /*
     * Method: editInfo
     * display the EditInfoModalWindow
     */
    editInfo: function() {
        if (fd().editPackageInfoModal) {
            log.debug('Only show 1 Properties Modal at a time.');
            return;
        }

        var controller = this;
        this.savenow = false;
        var modal = fd().editPackageInfoModal = fd().displayModal(
                string.substitute(settings.edit_package_info_template,
                    object.merge({}, this.data, this.options),
					/#\{([^{}]+)\}/g));

        modal.addListener('destroy', function() {
            delete fd().editPackageInfoModal;
        });
        dom.$('full_name').addListener('change', function() {
            fd().emit('change');
        });
        dom.$('package_description').addListener('change', function() {
            fd().emit('change');
        });
		

        var savenow = dom.$('savenow');
        if (savenow) {
            savenow.addListener('click', function() {
                controller.savenow = true;
            });
        }

        dom.$('UI_ActivateLink').getElement('a').addListener('click', this.makePublic.bind(this));
        dom.$('UI_DisableLink').getElement('a').addListener('click', this.makePrivate.bind(this));

        // adjust button if package has been made private or public
        // since last page load
        var pressedBtn, notPressedBtn;
        if (this.package_.get('active')) {
            pressedBtn = dom.$('UI_ActivateLink');
            notPressedBtn = dom.$('UI_DisableLink');
        } else {
            notPressedBtn = dom.$('UI_ActivateLink');
            pressedBtn = dom.$('UI_DisableLink');
        }
        pressedBtn.addClass('pressed').getElement('a').addClass('inactive');
        notPressedBtn.removeClass('pressed').getElement('a').removeClass('inactive');

        var fullNameValidator = new Validator('full_name', {
            pattern: /^[A-Za-z0-9\s\-_\.\(\)]*$/,
            message: 'Please use only letters, numbers, spaces, or "_().-" in this field.'
        });
		var jsonValidator = new JSONValidator('package_extra_json', {
			message: 'Must be blank or a valid JSON object.'
		});

		var package_extra_json_el = dom.$('package_extra_json');
		package_extra_json_el.store('json-validator', jsonValidator);
		if ('package_extra_json' in this.options) {
			package_extra_json_el.set('value', this.options.package_extra_json);
		}

		this._setupExtraPropertiesEditor();

		function isValid() {
			return fullNameValidator.validate() && jsonValidator.validate();
		}
        dom.$('package-info_form').addListener('submit', function(e) {
            e.stop();
            if (isValid()) {
                controller.submitInfo();
            } else {
                log.debug('Invalid name or JSON.');
            }
        });

        // Update modal from data (if not saved yet)
        object.forEach(this.data, function(value, key) {
            var el = dom.$(key);
            if (el) {
                el.set('value', value);
            }
        });
    },

	_setupExtraPropertiesEditor: function() {
		// Make package.json textarea an ACE editor
		var ace = require('ace/ace');

		var extra_json_el = dom.$('package_extra_json');
		extra_json_el.setStyle('display', 'none');
		var extra_json_editor_el = new dom.Element('div', {
			id: 'extra_json_ace',
			text: extra_json_el.get('value')
		});
		extra_json_editor_el.inject(extra_json_el, 'before');
		var editor = ace.edit(extra_json_editor_el.getNode());
		var editorSession = editor.getSession();
		editorSession.on('change', function() {
			extra_json_el.set('value', editorSession.getValue());
			fd().emit('change');
		});

		var validator = extra_json_el.retrieve('json-validator');
		editor.on('blur', function() {
			if (!validator.validate()) {
				validator.show();
			} else {
				validator.hide();
			}
		});
	},

    /*
     * Method: submitInfo
     * submit info from EditInfoModalWindow
     * if $('savenow') clicked - save the full info
     */
    submitInfo: function() {
        // collect data from the Modal
        this.options.package_info_form_elements.forEach(function(key) {
            var el = dom.$(key);
            if (el) {
                this.data[key] = el.get('value');
            }
        }, this);
        if (this.options.jid === this.data.jid) {
          delete this.data.jid;
        }
        // check if save should be called
        if (this.savenow) {
            return this.save();
        }
        fd().editPackageInfoModal.destroy();
    },

    collectData: function() {
        this.editor.dumpCurrent();
        this.data.version_name = dom.$('version_name').get('value');
        this.data.revision_message = dom.$('revision_message').get('value');
        object.forEach(this.modules, function(module, filename) {
            var mod = this.editor.getItem(module.get('uid'));
            if (!mod) {
                log.warn('Editor not found for module: %s', filename);
                return;
            }
            if (mod.get('content') && mod.changed) {
                this.data[filename] = mod.get('content');
            }
        }, this);
        object.forEach(this.attachments, function(attachment, uid) {
            var att = this.editor.getItem(uid);
            if (!att) {
                log.warn('Editor not found for attachment: %s', uid);
                return;
            }
            if (att.get('content') && att.changed) {
                this.data[attachment.get('uid')] = att.get('content');
            }
        }, this);
    },

    saveAction: function(e) {
        if (e) {
            e.stop();
        }
        this.save();
    },

    save: function() {
        if (this.saving) {
            log.debug('Already saving, stopping second request.');
            return;
        }
        var controller = this;
        this.collectData();
        this.saving = true;
        this.emit('saving');

        var loader = this.save_el;
        loader.addClass(LOADING_CLASS).addClass('small');
        return new Request({
            url: this.options.save_url,
            data: this.data,
            onFailure: function() {
                fd().error.alert(
                    string.substitute('Error {status}', this.xhr),
                    string.substitute('{statusText}<br/>{responseText}', this.xhr)
                );
            },
            onSuccess: function(text) {
                var response = JSON.parse(text);
                // set the redirect data to view_url of the new revision
                log.debug('Save succeeded');
                if (response.jid) {
                  this.package_.set('jid', response.jid);
                  this.options.jid = response.jid;
                }
                if (response.full_name) {
                    this.packageInfoNameEl.set('text', response.full_name);
                    this.options.full_name = response.full_name;
                    this.package_.set('full_name', response.full_name);
                }
                dom.$('revision_message').set('value', '');
                if (response.attachments_changed) {
                    object.forEach(response.attachments_changed,
                        function(options, uid) {
                            if (this.attachments[uid]) {
                                // updating attachment's uid
                                var att = this.attachments[uid];
                                att.set(options);
                            }
                        }, this
                    );
                }
                //fd().setURIRedirect(response.view_url);
                // set data changed by save
                this.registerRevision(response);
                fd().message.alert(response.message_title, response.message);
                // clean data leaving package_info data
                this.data = {};
                this.options.package_info_form_elements.forEach(function(key) {
                    if (response[key] != null) {
                        this.data[key] = response[key];
                    }
                }, this);
                if (fd().editPackageInfoModal) {
                    fd().editPackageInfoModal.destroy();
                }
                if (this.test_el && this.test_el.hasClass('pressed')) {
                    // only one add-on of the same id should be allowed on the Helper side
                    this.installAddon();
                }
                this.editor.cleanChangeState();
                this.emit('save');
            }.bind(this),
            onComplete: function() {
                loader.removeClass(LOADING_CLASS);
                controller.emit('save');
                controller.saving = false;
            }
        }).send();
    },

    blur: function() {
        this._focused = false;
        this.editor.blur();
        this.emit('blur');
        this.editor.addListener('focus:once', function() {
            if (!this._focused) {
                this.focus();
            }
        }.bind(this));
    },
    
    _focused: true,

    focus: function() {
        if (this._focused) {
            return;
        }
        this._focused = true;
        //this.keyboard.activate();
        this.editor.focus();
        
        this.emit('focus');
    },

    bind_keyboard: function() {
        var controller = this;

        dom.document.addListener('keyup', function(e) {
            if (!e.control) {
                return;
            }
            switch (e.key) {
                case 's':
                    e.preventDefault();
                    controller.save();
                    break;
                case 'enter':
                    if (controller.package_.isAddon()) {
                        e.preventDefault();
                        controller.testAddon();
                    }
                    break;
                case 'n':
                    e.preventDefault();
                    if (e.shift) {
                        //new module
                        controller.sidebar.promptNewFile();
                    } else {
                        //new attachment
                        controller.sidebar.promptAttachment();
                    }
                    break;
                // keypress isn't fired for this key... awesome!
                case '/':
                    if (e.shift) {
                        e.preventDefault();
                        controller.toggleShortcutsModal();
                    }
                    break;
            }
        });

        // to prevent Firefox's default shortcuts
        // keyup is too late
        dom.document.addListener('keypress', function(e) {
            if (e.control && (e.key === 'n'|| e.key === 's')) {
                e.preventDefault();
            }
        });
    },
    
    toggleShortcutsModal: function() {
        if (this._shortcutsModal) {
            this.hideShortcuts();
        } else {
            this.showShortcuts();
        }
    },
    
    showShortcuts: function() {
        var shortcuts = [
            {
                keys: 'ctrl+s',
                description: 'Save current outstanding changes.'
            },
            {
                keys: 'ctrl+enter',
                description: 'Toggle testing.'
            },
            {
                keys: 'ctrl+shift+n',
                description: 'Open the new Module prompt.'
            },
            {
                keys: 'ctrl+n',
                description: 'Open the new Attachment prompt.'
            },
            {
                keys: 'ctrl+shift+/',
                description: 'Show these keyboard shortcuts.'
            }
        ];
        var output = [];
        function buildLines(shortcut) {
            var keys = '<kbd>'+ shortcut.keys.split('+').join('</kbd> + <kbd>').split('|').join('</kbd> or <kbd>').replace(/meta/g, 'cmd') + '</kbd>';
            output.push(keys + ': ' + shortcut.description);
        }
        
        
        //output.push('<strong>Editor</strong>');
        shortcuts.forEach(buildLines);
        //shortcuts.push('<strong>Tree</strong>');
        //this.sidebar.keyboard.getShortcuts().forEach(buildLines);
        
        this._shortcutsModal = fd().displayModal('<h3>Keyboard Shortcuts</h3>'+
                        '<div class="UI_Modal_Section"><p>'+
                        output.join('</p><p>')+
                        '</p></div>'+
                        '<div class="UI_Modal_Actions">'+
                            '<ul>'+
                                '<li><input type="reset" class="closeModal" value="Close"/></li>'+
                            '</ul>'+
                        '</div>'
        );
        this._shortcutsModal.addListener('destroy', function() {
            this._shortcutsModal = null;
        }.bind(this));
    },
    
    hideShortcuts: function() {
        if (this._shortcutsModal) {
            this._shortcutsModal.destroy();
        }
    },

    registerRevision: function(urls) {
        // update page title to reflect current revision and name
        var title = dom.document.get('title');
        dom.document.set('title', title.replace(this.options.revision_string, urls.revision_string));
        this.setOptions(urls);
        this.package_.set(urls);
        // this only for the right display href
        if (urls.download_url && this.download_el) {
            this.download_el.set('href', urls.download_url);
        }
    },

    alertUnsavedData: function(e) {
        if (this.edited && !fd().saving) {
            e.preventDefault();
        }
    }

});
