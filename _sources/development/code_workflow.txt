.. _code-workflow:

=============
Code Workflow
=============

Please read for a detailed explanation.

.. figure:: ../../../_static/workflow.png
    :width: 250px

    Code Workflow Diagram [:ref:`full size <code_workflow_diagram>`]

Decentralized, but Centralized
==============================
Every developer has his own repository called ``origin`` in this and following documents. There is only one referencing repository called ``main``. It will contain ``production``, ``staging``, ``release-*`` and ``master`` branches.


Main branches
=============

Main repository holds 3 infinite and a number of short term ``release-*`` branches.

production
----------
Must branch off from: ``staging``

This is the branch from which one can get a production ready code.

staging
-------
Must branch off from: ``release-*``
Must branch back to: ``production``

This is the pranch from which a staging server can get the code.

release-*
---------
Must branch off from: ``master``
Takes merges from: ``master`` or ``hotfix-*``
Branches back to: ``staging``

This is the branch used to prepare the release. When we think it is ready, ``staging`` branch merges from it.

master
------
Takes merges from: ``bug-*``, ``hotfix-*``

This is our development branch - every developer's repository should have their ``origin/master`` in sync with the ``main/master``.

Origin branches
===============

Origin is the remote repository owned by the developer

master
------
Updates from ``main/master``
May merge back from ``bug-*`` and ``hotfix-*`` to provide local master repositories. It's usually not to be shared with other developers, especially ``main/master`` will not merge from it.

bug-*
-----
Must branch of from ``master`` or other ``bug-*``.
Naming convention: ``bug-{bugzilla_bug_id}-short_description``

Fixes one bug. It will be merged to the ``main`` repository by :ref:`Repository Manager <repository-manager>`

hotfix-*
--------
Must branch off from ``main/release-*``, ``main/staging`` or ``main/production``
Merges back to ``main/staging`` or ``main/production``

This branch is for fixes to freezed code - in staging or production

.. References`

.. _`Vincent Driessen's model`: http://nvie.com/git-model
