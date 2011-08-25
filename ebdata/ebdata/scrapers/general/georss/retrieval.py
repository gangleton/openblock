#!/usr/bin/env python
# encoding: utf-8

#   Copyright 2011 OpenPlans and contributors
#
#   This file is part of OpenBlock
#
#   OpenBlock is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   OpenBlock is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with OpenBlock.  If not, see <http://www.gnu.org/licenses/>.
#


"""A quick and dirty scraper for RSS feeds with GeoRSS info.
"""

import datetime
import feedparser
from httplib2 import Http
import logging

from django.contrib.gis.geos import Point
from ebdata.nlp.addresses import parse_addresses
from ebpub.db.models import NewsItem, Schema
from ebpub.geocoder import SmartGeocoder
from ebpub.geocoder.base import GeocodingException
from ebpub.utils.logutils import log_exception
from ebpub.utils.geodjango import intersects_metro_bbox

# Note there's an undocumented assumption in ebdata that we want to
# unescape html before putting it in the db.
from ebdata.retrieval.utils import convert_entities

logger = logging.getLogger()


class LocalNewsScraper(object):

    def __init__(self, url, schema_slug='local-news', http_cache=None):
        self.url = url
        self.schema_slug=schema_slug
        self.http = Http(http_cache)

    def update(self):

        logger.info("Starting LocalNewsScraper update %s" % self.url)

        try:
            schema = Schema.objects.get(slug=self.schema_slug)
        except Schema.DoesNotExist:
            logger.error( "Schema (%s): DoesNotExist" % self.schema_slug)
            return 1

        response, content = self.http.request(self.url)
        if response.fromcache:
            logger.info("Feed is unchanged since last update (cached)")
            return

        f = feedparser.parse(content)
        addcount = updatecount = 0
        for entry in f.entries:
            title = convert_entities(entry.title)
            description = convert_entities(entry.description)

            if entry.id.startswith('http'):
                item_url = entry.id
            else:
                item_url = entry.link
            try:
                item = NewsItem.objects.get(schema__id=schema.id,
                                            title=title,
                                            description=description)
                #url=item_url)
                status = 'updated'
            except NewsItem.DoesNotExist:
                item = NewsItem()
                status = 'added'
            except NewsItem.MultipleObjectsReturned:
                # Seen some where we get the same story with multiple URLs. Why?
                logger.warn("Multiple entries matched title %r and description %r. Expected unique!" % (title, description))
                continue
            try:
                item.title = title
                item.schema = schema
                item.description = description
                item.url = item_url
                item.location_name = entry.get('x-calconnect-street') or entry.get('georss_featurename')
                item.item_date = datetime.datetime(*entry.updated_parsed[:6])
                item.pub_date = datetime.datetime(*entry.updated_parsed[:6])

                # feedparser bug: depending on which parser it magically uses,
                # we either get the xml namespace in the key name, or we don't.
                point = entry.get('georss_point') or entry.get('point')
                x, y = None, None
                if point:
                    x, y = point.split(' ')
                _short_title = item.title[:30] + '...'
                if True:
                    # Fall back on geocoding.
                    text = item.title + ' ' + item.description
                    addrs = parse_addresses(text)
                    for addr, unused in addrs:
                        try:
                            result = SmartGeocoder().geocode(addr)
                            point = result['point']
                            logger.debug("internally geocoded %r" % addr)
                            x, y = point.x, point.y
                            break
                        except GeocodingException:
                            logger.debug("Geocoding exception on %r:" % text)
                            log_exception(level=logging.DEBUG)
                            continue
                        except:
                            logger.error('uncaught geocoder exception on %r\n' % addr)
                            log_exception()
                    if None in (x, y):
                        logger.debug("couldn't geocode any addresses in item '%s...'"
                                     % _short_title)
                        continue
                item.location = Point((float(x), float(y)))
                if not intersects_metro_bbox(item.location):
                    reversed_loc = Point((float(y), float(x)))
                    if intersects_metro_bbox(reversed_loc):
                        logger.info(
                            "Got points in apparently reverse order, flipping them")
                        item.location = reversed_loc
                    else:
                        logger.info("Skipping %r as %s,%s is out of bounds" %
                                    (_short_title, y, x))
                        continue
                if not item.location_name:
                    # Fall back to reverse-geocoding.
                    from ebpub.geocoder import reverse
                    try:
                        block, distance = reverse.reverse_geocode(item.location)
                        logger.debug(" Reverse-geocoded point to %r" % block.pretty_name)
                        item.location_name = block.pretty_name
                        item.block = block
                    except reverse.ReverseGeocodeError:
                        logger.debug(" Failed to reverse geocode %s for %r" % (item.location.wkt, _short_title))
                        item.location_name = u''
                item.save()
                if status == 'added':
                    addcount += 1
                else:
                    updatecount += 1
                logger.info("%s: %s" % (status, _short_title))
            except:
                logger.error("Warning: couldn't save %r. Traceback:" % _short_title)
                log_exception()
        logger.info("Finished LocalNewsScraper update: %d added, %d updated" % (addcount, updatecount))

def main(argv=None):
    import sys
    if argv is None:
        argv = sys.argv[1:]

    from optparse import OptionParser
    usage = "usage: %prog [options] <feed url>"
    parser = OptionParser(usage=usage)

    parser.add_option(
        "--schema", help="which news item type to create when scraping",
        default="local-news"
        )
    parser.add_option(
        "--http-cache", help='location to use as an http cache.  If a cached value is seen, no update is performed.', 
        action='store'
        )

    options, args = parser.parse_args(argv)
    
    if len(args) < 1:
        parser.print_usage()
        sys.exit(0)
    
    scraper = LocalNewsScraper(url=args[0],  schema_slug=options.schema, http_cache=options.http_cache)

    scraper.update()

    

if __name__ == '__main__':
    main()
