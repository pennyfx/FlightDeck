.. _elasticsearch:

Elastic Search
==============

`ElasticSearch`__  is a Lucene based search engine
that powers FlightDeck search. We also use ``pyes`` (link_) a pythonic interface to
ElasticSearch.

__ http://elasticsearch.org/
.. _link: https://github.com/aparo/pyes

Running ElasticSearch
---------------------

FlightDeck was developed with ElasticSearch 14.4 so we recommend downloading
that and running it.  You will need to point it at a config file that we've
included in ``scripts/es/es.yml``::

    elasticsearch -f -Des.config=$ROOT/scripts/es/es.yml

Where ``$ROOT`` is your FlightDeck home.

Configuration
-------------

This configuraion can be overridden if necessary.  FlightDeck by default uses
port 9201 and 9301.  More details are `here`__.

__ http://www.elasticsearch.org/guide/reference/setup/configuration.html

Development
-----------

``settings.py`` needs to be overridden in order to use ElasticSearch.  Both
``ES_DISABLED`` needs to be ``False`` and ``ES_HOSTS`` needs to be set.  This
can be done in ``settings_local.py``.

Testing
-------

In order for testing to work ``ES_HOSTS`` needs to be defined (otherwise
SkipTest will be raised) and ElasticSearch needs to be running.  We
specifically look at a single index, ``test_flightdeck``, in order to avoid
conflicts with development data.

Todo
----

In the future we may need to:

* Add items and remove items asynchronously using Celery.
* Build a frontend for search.
* Add custom mapping.
