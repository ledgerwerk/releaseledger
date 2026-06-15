Quickstart
==========

Install
-------

.. code-block:: bash

   python -m pip install releaseledger

For development:

.. code-block:: bash

   python -m pip install -e ".[dev]"

Initialize a project
--------------------

.. code-block:: bash

   releaseledger init

This creates ``.releaseledger.toml`` and the default state layout:

.. code-block:: text

   .releaseledger/
     ledgers/
       main/
         releases/
         events/
         indexes/

Releaseledger is git-first. The recommended workflow uses git commit ranges
as the canonical evidence of shipped changes.

Create a release and attach the git range
------------------------------------------

.. code-block:: bash

   releaseledger release create 1.2.0 \
     --previous 1.1.0 \
     --released-at 2026-06-14

   releaseledger release update 1.2.0 \
     --git-base v1.1.0 \
     --git-head HEAD

Generate entries from git commits
---------------------------------

.. code-block:: bash

   releaseledger git import 1.2.0 \
     --base v1.1.0 \
     --head HEAD \
     --status draft \
     --output /tmp/1.2.0-entries.yaml

Edit the YAML to curate summaries, then:

.. code-block:: bash

   releaseledger entry add-many 1.2.0 --file /tmp/1.2.0-entries.yaml --dry-run
   releaseledger entry add-many 1.2.0 --file /tmp/1.2.0-entries.yaml

Review git coverage and build the changelog
--------------------------------------------

.. code-block:: bash

   releaseledger review 1.2.0 --git --strict
   releaseledger build 1.2.0 \
     --release-date 2026-06-14 \
     --strict \
     --target-file CHANGELOG.md

Optional: taskledger provenance
-------------------------------

Taskledger refs (``tl:task-0103``) and PR refs (``github:pr-42``) are optional
provenance. Add them to entries to enrich coverage, but git commits are the
primary source of truth:

.. code-block:: bash

   releaseledger entry add 1.2.0 \
     --kind added \
     --summary "Added release bundle storage" \
     --status accepted \
     --source-ref git:abcdef0123456789abcdef0123456789abcdef01 \
     --source-ref tl:task-0103

Create a release
----------------

.. code-block:: bash

   releaseledger release create 1.2.0 \
     --title "Release 1.2.0" \
     --boundary-ref tl:task-0105 \
     --source-ref tl:task-0103

Add entries
-----------

.. code-block:: bash

   releaseledger entry add 1.2.0 \
     --kind added \
     --summary "Added release bundle storage" \
     --status accepted \
     --source-ref tl:task-0103

Validate entries:

.. code-block:: bash

   releaseledger entry lint 1.2.0 --strict

Render changelog output
-----------------------

Use ``changelog`` to produce review context:

.. code-block:: bash

   releaseledger changelog 1.2.0 \
     --target-changelog CHANGELOG.md \
     --release-date 2026-06-13

Use ``build`` to render and insert a final section:

.. code-block:: bash

   releaseledger build 1.2.0 \
     --dry-run \
     --strict \
     --target-file CHANGELOG.md

   releaseledger build 1.2.0 \
     --release-date 2026-06-13 \
     --strict \
     --target-file CHANGELOG.md
