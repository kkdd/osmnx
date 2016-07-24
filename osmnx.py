# The MIT License (MIT)
# 
# Copyright (c) 2016 Geoff Boeing http://geoffboeing.com
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
###################################################################################################
# Module: osmnx.py
# Description: Retrieve and construct spatial geometries and street networks from OpenStreetMap
###################################################################################################

import json, math, os, re, time, datetime as dt, logging as lg
import requests, geopandas as gpd, networkx as nx, matplotlib.pyplot as plt
from matplotlib import collections as mc
from shapely.geometry import Point
from geopy.distance import great_circle, vincenty
from geopy.geocoders import Nominatim

_data_folder = 'osmnx_data'
_logs_folder = 'osmnx_logs'
_imgs_folder = 'osmnx_images'
_cache_folder = 'osmnx_cache'
_cache_filename = 'response_cache.json'

_file_log = False
_print_log = False


def init(data_folder=_data_folder, logs_folder=_logs_folder, imgs_folder=_imgs_folder, 
         cache_folder=_cache_folder, cache_filename=_cache_filename,
         file_log=_file_log, print_log=_print_log):
    """
    Initialize the tool.
    
    Arguments:
    data_folder = where to save and load data files
    logs_folder = where to write the log files
    imgs_folder = where to save figures
    cache_folder = where to save the http request cache
    cache_filename = what to name the cache file
    file_log = if true, save log output to a log file in logs_folder
    print_log = if true, print log output to the console
    
    Returns: None
    """
    
    global _cache_folder, _cache_filename, _data_folder, _imgs_folder, _logs_folder, _print_log, _file_log
    _cache_folder = cache_folder
    _cache_filename = cache_filename
    _data_folder = data_folder
    _imgs_folder = imgs_folder
    _logs_folder = logs_folder
    _print_log = print_log
    _file_log = file_log
    if _file_log:
        log('Initialized osmnx')


def log(message, level=lg.INFO):
    """
    
    """
    if _file_log:
        logger = get_logger()
        if level == lg.DEBUG:
            logger.debug(message)
        elif level == lg.INFO:
            logger.info(message)
        elif level == lg.WARNING:
            logger.warning(message)
        elif level == lg.ERROR:
            logger.error(message)
            
    if _print_log:
        print(message)


def get_logger(name='osmnx', level=lg.INFO):
    """
    Create a logger to capture progress.
    
    Arguments:
    name = name of the logger
    level = logging level
    
    Returns: logger
    """
    logger = lg.getLogger(name)
    if not getattr(logger, 'handler_set', None):
        todays_date = dt.datetime.today().strftime('%Y_%m_%d')#_%H_%M_%S')
        log_filename = '{}/{}_{}.log'.format(_logs_folder, name, todays_date)
        if not os.path.exists(_logs_folder):
            os.makedirs(_logs_folder)
        handler = lg.FileHandler(log_filename, encoding='utf-8')
        formatter = lg.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.handler_set = True
    return logger

    
def save_to_cache(url, params, response_json):
    """
    
    """
    if response_json is None:
        log('Saved nothing to cache because response is None')
    else:        
        # create the folder on the disk if it doesn't already exist
        if not os.path.exists(_cache_folder):
            os.makedirs(_cache_folder)

        # open the cache file if it already exists, otherwise create a new dict
        cache_path_filename = '{}/{}'.format(_cache_folder, _cache_filename)
        cache = json.load(open(cache_path_filename)) if os.path.isfile(cache_path_filename) else {}
        
        # created a sorted list of url params so we don't get multiple cache entries for the same URL just with params in different order
        param_str = ''.join(['{}={}&'.format(key, params[key]) for key in sorted(list(params.keys()))]).strip('&')
        url_params = '{}?{}'.format(url, param_str)
        
        # strip any timeout data from the url - we don't need multiple copies with different timeouts
        key = re.sub('(%5Btimeout.*?%5D)', '', url_params)

        # add this url to the cache in memory, with value of response
        cache[key] = response_json

        # save the cache to disk
        with open(cache_path_filename, 'w', encoding='utf-8') as cache_file:
            cache_file.write(json.dumps(cache))
        log('Saved {:,} cached responses to {}'.format(len(cache.keys()), cache_path_filename))
        

def get_from_cache(url, params, display_url=''):
    """
    
    """
    # open the cache file if it already exists, otherwise create a new dict
    cache_path_filename = '{}/{}'.format(_cache_folder, _cache_filename)
    cache = json.load(open(cache_path_filename)) if os.path.isfile(cache_path_filename) else {}
    
    # cache keys are based on a sorted list of url params (see comment in save_to_cache function)
    param_str = ''.join(['{}={}&'.format(key, params[key]) for key in sorted(list(params.keys()))]).strip('&')
    url_params = '{}?{}'.format(url, param_str)
    
    if url_params in cache:
        log('Retrieved response from cache for URL: {}'.format(display_url))
        return cache[url_params]
        
        
def make_request(url, params=None, pause_duration=1, timeout=30):
    
    # you have to pass a timeout to the overpass api for longer queries. add this here,
    # making it match the timeout that requests is using (the value passed in the func arg)
    # we recursively pass params_original to make_request each time the request times out 
    # so we can increase the timeout interval for both requests and reformat the string for 
    # the overpass server parameter
    params_original = params.copy()
    if isinstance(params, dict) and 'data' in params and '[timeout:{timeout}]' in params['data']:
        params['data'] = params['data'].format(timeout=timeout)
    
    prepared_url = requests.Request('GET', url, params=params).prepare().url
    cached_response_json = get_from_cache(url, params, prepared_url)
    
    if not cached_response_json is None:
        return cached_response_json
    else:
        log('Pausing {:,.2f} seconds before making API request'.format(pause_duration))
        time.sleep(pause_duration)
        start_time = time.time()
        log('Requesting {} with timeout={}'.format(prepared_url, timeout))
        try:
            response = requests.get(url, params, timeout=timeout)
            size_kb = len(response.content) / 1000.0
            domain = re.findall(r'//(?s)(.*?)/', url)[0]
            log('Downloaded {:,.1f}KB from {} in {:,.2f} seconds'.format(size_kb, domain, time.time()-start_time))
            response_json = response.json()
            save_to_cache(url, params, response_json)
        except requests.exceptions.Timeout:
            log('Request timed out after {:,.2f} seconds. Increasing timeout interval and re-trying.'.format(time.time()-start_time), level=lg.WARNING)
            response = make_request(url=url, params=params_original, pause_duration=pause_duration, timeout=timeout*2)
            response_json = response.json()
        
        return response_json


def osm_polygon_download(query, limit=1, polygon_geojson=1, pause_duration=1):
    """
    Geocode a place and download its boundary geometry from OSM's Nominatim API.
    
    Arguments:
    query = either a string or dict (conataining a structured query) to geocode/download
    limit = max number of results to return
    polygon_geojson = request the boundary geometry polygon from the API, 0=no, 1=yes
    pause_duration = time in seconds to pause before API requests
    
    Returns: dict
    """
    # define the Nominatim API endpoint and parameters
    url = 'https://nominatim.openstreetmap.org/search'
    params = {'format':'json',
              'limit':limit,
              'polygon_geojson':polygon_geojson}
    
    # add the structured query dict (if provided) to params, otherwise query with place name string
    if isinstance(query, str):
        params['q'] = query
    elif isinstance(query, dict):
        for key in query:
            params[key] = query[key]
    else:
        raise ValueError('query must be a dict or a string')
    
    # request the URL, return the JSON
    response_json = make_request(url, params)
    return response_json
    

def gdf_from_place(query, gdf_name=None, which_result=1):
    """
    Create a GeoDataFrame from a single place name query.
    
    Arguments:
    query = either a string or dict (containing a structured query) to geocode/download
    gdf_name = string to use as name attribute metadata for GeoDataFrame (this is used to save shapefile later)
    which_result = max number of results to return and which to process upon receipt
    
    Returns: GeoDataFrame
    """
    # if no gdf_name is passed, just use the query
    assert (isinstance(query, dict) or isinstance(query, str)), 'query must be a dict or a string'
    if (gdf_name is None) and isinstance(query, dict):
        gdf_name = ', '.join(list(query.values()))
    elif (gdf_name is None) and isinstance(query, str):
        gdf_name = query
    
    # get the data from OSM
    data = osm_polygon_download(query, limit=which_result)
    if len(data) >= which_result:
        
        # extract data elements from the JSON response
        bbox_south, bbox_north, bbox_west, bbox_east = [float(x) for x in data[which_result - 1]['boundingbox']]
        geometry = data[which_result - 1]['geojson']
        place = data[which_result - 1]['display_name']
        features = [{'type': 'Feature',
                     'geometry': geometry,
                     'properties': {'place_name': place,
                                    'bbox_north': bbox_north,
                                    'bbox_south': bbox_south,
                                    'bbox_east': bbox_east,
                                    'bbox_west': bbox_west}}]
        
        # if we got an unexpected geometry type (like a point), log a warning
        if geometry['type'] not in ['Polygon', 'MultiPolygon']:
            log('OSM returned a {} as the geometry.'.format(geometry['type']), level=lg.WARNING)
        
        # create the GeoDataFrame, name it, and return it
        gdf = gpd.GeoDataFrame.from_features(features)
        gdf.name = gdf_name
        log('Created GeoDataFrame with {} row for query "{}"'.format(len(gdf), query))
        return gdf
    else:
        # if there was no data returned
        log('OSM returned no results for query "{}"'.format(query), level=lg.WARNING)
        gdf = gpd.GeoDataFrame()
        gdf.name = gdf_name
        return gdf
        

def gdf_from_places(queries, gdf_name='unnamed'):
    """
    Create a GeoDataFrame from a  list of place names to query.
    
    Arguments:
    queries = a list of strings and/or dicts representing places to geocode/download
    gdf_name = string to use as name attribute metadata for GeoDataFrame (this is used to save shapefile later)
    
    Returns: GeoDataFrame
    """
    # create an empty GeoDataFrame then append each result as a new row
    gdf = gpd.GeoDataFrame()
    for query in queries:
        gdf = gdf.append(gdf_from_place(query))
        
    # reset the index, name the GeoDataFrame, then return it
    gdf = gdf.reset_index().drop(labels='index', axis=1)
    gdf.name = gdf_name
    log('Finished creating GeoDataFrame with {} rows from {} queries'.format(len(gdf), len(queries)))
    return gdf


def make_shp_filename(place_name):
    """
    Create a filename string in a consistent format from a place name string.
    
    Arguments:
    place_name = place name to convert into a filename string
    
    Returns: string
    """
    name_pieces = list(reversed(place_name.split(', ')))
    filename = '-'.join(name_pieces).lower().replace(' ','_')
    filename = re.sub('[^0-9a-zA-Z_-]+', '', filename)
    return '{}.shp'.format(filename)


def save_shapefile(gdf):
    """
    Save GeoDataFrame as an ESRI shapefile.
    
    Arguments:
    gdf = the GeoDataFrame to save
    data_folder = folder in which to save it
    
    Returns: None
    """
    filename = make_shp_filename(gdf.name)
    folder_path = '{}/{}'.format(_data_folder, filename)
    file_path_name = '{}/{}'.format(folder_path, filename)
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
    gdf.to_file(file_path_name)
    log('Saved the GeoDataFrame "{}" as shapefile "{}"'.format(gdf.name, file_path_name))
 

def project_utm(gdf):
    """
    Project a GeoDataFrame to the UTM zone appropriate for its geometries' centroid.
    
    Arguments:
    gdf = the GeoDataFrame to project
    
    Returns: GeoDataFrame
    """
    assert len(gdf) > 0, 'You cannot project an empty GeoDataFrame.'
    start_time = time.time()
    
    # if GeoDataFrame is already in UTM, just return it
    if (not gdf.crs is None) and ('proj' in gdf.crs) and (gdf.crs['proj'] == 'utm'):
        return gdf
    
    # calculate the centroid of the union of all the geometries in the GeoDataFrame
    avg_longitude = gdf['geometry'].unary_union.centroid.x
    
    # calculate the UTM zone from this avg longitude and define the UTM CRS to project
    utm_zone = math.floor((avg_longitude + 180) / 6.0) + 1
    utm_crs = {'datum': 'NAD83',
               'ellps': 'GRS80',
               'proj' : 'utm',
               'zone' : utm_zone,
               'units': 'm'}
    
    # set the original CRS of the GeoDataFrame to lat-long
    gdf.crs = {'init':'epsg:4326'}
    
    # project the GeoDataFrame to the UTM CRS
    projected_gdf = gdf.to_crs(utm_crs)
    projected_gdf.name = gdf.name
    log('Projected the GeoDataFrame "{}" to UTM {} in {:,.2f} seconds'.format(projected_gdf.name, utm_zone, time.time()-start_time))
    return projected_gdf


    
##########################################################################################################        
#
# End of functions for getting place boundary geometries.
#
# Below are functions for getting and processing street networks.
#
#########################################################################################################    
   
 
 
def osm_net_download(north, south, east, west, pause_duration=1, timeout=180):
    """
    Download OSM ways and nodes within some bounding box from the Overpass API.
    
    Arguments:
    north = northern latitude of bounding box
    south = southern latitude of bounding box
    east = eastern longitude of bounding box
    west = western longitude of bounding box
    pause_duration = time to pause in seconds between API requests
    
    Returns: dict
    """
    url = 'http://www.overpass-api.de/api/interpreter'
    filters='"highway"!~"motor"' # network type is walking, so exclude things like freeways
    data = '[out:json][timeout:{{timeout}}];( way ["highway"] [{filters}] ({south},{west},{north},{east}); >;);out;'
    
    # format everything but timeout here. we pass timeout int along to make_request where it adds it 
    # to the params dict so that make_request can call itself recursively, increasing the timeout interval each
    # time, if the query is really big and causing this time limit to timeout.
    data = data.format(filters=filters, south=south, west=west, north=north, east=east) #bbox as 'south,west,north,east'
    params = {'data':data}
    
    # request the URL, return the JSON
    response_json = make_request(url, params, timeout=timeout)
    return response_json
    

def get_node(element):
    """
    Convert an OSM node element into the format for a networkx node.
    
    Arguments:
    element = an OSM node element
    
    Returns: dict
    """
    node = {}
    node['lat'] = element['lat']
    node['lon'] = element['lon']
    if 'tags' in element:
        node['highway'] = element['tags']['highway'] if 'highway' in element['tags'] else None
    return node
    
    
def get_path(element):
    """
    Convert an OSM way element into the format for a networkx graph path.
    
    Arguments:
    element = an OSM way element
    
    Returns: dict
    """
    path = {}
    path['nodes'] = element['nodes']
    if 'tags' in element:
        path['name'] = element['tags']['name'] if 'name' in element['tags'] else None
        path['city'] = element['tags']['addr:city'] if 'addr:city' in element['tags'] else None
        path['highway'] = element['tags']['highway'] if 'highway' in element['tags'] else None
        path['maxspeed'] = element['tags']['maxspeed'] if 'maxspeed' in element['tags'] else None
    return path
    

def parse_osm_nodes_paths(osm_data):
    """
    Construct dicts of nodes and paths with key=osmid and value=dict of attributes.
    
    Arguments:
    osm_data = JSON response from from the Overpass API
    
    Returns: tuple
    """
    nodes = {}
    paths = {}
    for element in osm_data['elements']:
        if element['type'] == 'node':
            key = element['id']
            nodes[key] = get_node(element)
        elif element['type'] == 'way': #osm calls network paths 'ways'
            key = element['id']
            paths[key] = get_path(element)
    
    return nodes, paths
    
    
def remove_orphan_nodes(G):
    """
    Remove from a graph all the nodes that have no edges.
    
    Arguments:
    G = networkx graph
    
    Returns: networkx graph
    """
    degree = G.degree()
    orphaned_nodes = [node for node in degree.keys() if degree[node] < 1]
    G.remove_nodes_from(orphaned_nodes)
    log('Removed {:,} orphaned nodes'.format(len(orphaned_nodes)))
    return G
    
   
def get_largest_subgraph(G, retain_all=False):
    """
    Return the largest connected subgraph from a graph.
    
    Arguments:
    G = networkx graph
    retain_all = if True, return the entire graph even if it is not connected
    
    Returns: networkx graph
    """
    # if the graph is not connected and caller did not request retain_all, retain only the largest connected subgraph
    if (not retain_all) and (not nx.is_connected(G)):
        original_len = len(G.nodes())
        G = max(nx.connected_component_subgraphs(G), key=len)
        log('Graph was not connected, retained only the largest connected subgraph ({:,} of {:,} total nodes)'.format(len(G.nodes()), original_len))
    return G
    

def truncate_graph_dist(G, source_node, max_distance=1000, weight='length', retain_all=False):
    """
    Remove everything further than some network distance from a specified node in graph.
    
    Arguments:
    G = a networkx graph
    source_node = the node in the graph from which to measure network distances to other nodes
    max_distance = remove every node in the graph greater than this distance from the source_node
    weight = how to weight the graph when measuring distance (default 'length' is how many meters long the edge is)
    
    Returns: networkx graph
    """
    start_time = time.time()
    distances = nx.shortest_path_length(G, source=source_node, weight=weight)
    distant_nodes = {key:value for key, value in distances.items() if value > max_distance}
    G.remove_nodes_from(distant_nodes.keys())
    log('Truncated graph by weighted network distance in {:,.2f} seconds'.format(time.time()-start_time))
    
    # remove any orphaned nodes, keep only the largest subgraph (if retain_all is True), and return G
    G = remove_orphan_nodes(G)
    G = get_largest_subgraph(G, retain_all)
    return G
    
    
def truncate_graph_bbox(G, north, south, east, west, retain_all=False):
    """
    Remove every node in graph that falls outside a bounding box. Needed because osm seems to return ways that 
    include nodes outside the bbox if the way has a node inside the bbox at some point.
    
    Arguments:
    G = a networkx graph
    north = northern latitude of bounding box
    south = southern latitude of bounding box
    east = eastern longitude of bounding box
    west = western longitude of bounding box
    
    Returns: networkx graph
    """
    start_time = time.time()
    nodes_outside_bbox = []
    for node_id, node in G.nodes(data=True):
        if node['lat'] > north or node['lat'] < south or node['lon'] > east or node['lon'] < west:
            nodes_outside_bbox.append(node_id)
    G.remove_nodes_from(nodes_outside_bbox)
    log('Truncated graph by bounding box in {:,.2f} seconds'.format(time.time()-start_time))
    
    # remove any orphaned nodes, keep only the largest subgraph (if retain_all is True), and return G
    G = remove_orphan_nodes(G)
    G = get_largest_subgraph(G, retain_all)
    return G
    

def truncate_graph_polygon(G, polygon, x='lon', y='lat', retain_all=False):
    """
    Remove every node in graph that falls outside some shapely Polygon or MultiPolygon.
    
    Arguments:
    G = networkx graph to truncate
    polygon = a shapely Polygon or MultiPolygon, only retain nodes in graph that lie within this shape
    x = node attribute to use as x coordinate
    y = node attribute to use as y coordinate
    retain_all = if True, return the entire graph even if it is not connected
    
    Returns: networkx graph
    """
    # find all the nodes in the graph that lie outside the polygon
    start_time = time.time()
    log('Identifying all nodes that lie outside the polygon')
    geometry = [Point(data[x], data[y]) for _, data in G.nodes(data=True)]
    gdf_nodes = gpd.GeoDataFrame({'node_id':G.nodes(), 'geometry':geometry})
    nodes_outside_polygon = gdf_nodes[~gdf_nodes.intersects(polygon)]
    log('Found {:,} nodes outside polygon in {:,.2f} seconds'.format(len(nodes_outside_polygon), time.time()-start_time))
    
    # now remove from the graph all those nodes that lie outside the place polygon
    start_time = time.time()
    G.remove_nodes_from(nodes_outside_polygon['node_id'])
    log('Truncated graph by polygon in {:,.2f} seconds'.format(time.time()-start_time))
    
    # remove any orphaned nodes, keep only the largest subgraph (if retain_all is True), and return G
    G = remove_orphan_nodes(G)
    
    G = get_largest_subgraph(G, retain_all)
    
    return G
    
    
def add_edge_lengths(G):
    """
    Add length (meters) attribute to each edge by great circle distance between vertices u and v.
    
    Arguments:
    G = a networkx graph
    
    Returns: networkx graph
    """
    for u, v in G.edges():
        u_point = (G.node[u]['lat'], G.node[u]['lon'])
        v_point = (G.node[v]['lat'], G.node[v]['lon'])
        edge_length = great_circle(u_point, v_point).m #geopy points are (lat, lon)
        G[u][v]['length'] = edge_length
    return G
    

def get_nearest_node(G, point, return_dist=False):
    """
    Return the graph node nearest to the specified point.
    
    Arguments:
    G = a networkx graph
    point = the (lat, lon) point for which we will find the nearest node in the graph
    return_dist = optionally also return the distance between the point and the nearest node
    
    Returns: networkx node or tuple
    """
    start_time = time.time()
    nodes = G.nodes(data=True)
    nearest_node = min(nodes, key=lambda node: great_circle((node[1]['lat'], node[1]['lon']), point).m)
    log('Found nearest node ({}) to point {} in {:,.2f} seconds'.format(nearest_node[0], point, time.time()-start_time))
    
    if return_dist:
        distance = great_circle((nearest_node[1]['lat'], nearest_node[1]['lon']), point).m #geopy points are (lat, lon) not (x, y)
        return nearest_node[0], distance
    else:
        return nearest_node[0]

        
def create_graph(osm_data, name='unnamed', retain_all=True):
    """
    Create a networkx graph from OSM data.
    
    Arguments:
    osm_data = JSON response from from the Overpass API
    name = the name of the graph
    retain_all = if true, retain all subgraphs, if false, retain only the largest connected subgraph
    
    Returns: networkx graph
    """
    log('Creating networkx graph from downloaded OSM data')
    start_time = time.time()
    G = nx.Graph(name=name)
    nodes, paths = parse_osm_nodes_paths(osm_data)
    
    # add each osm node to the graph
    for node_id, node in nodes.items():
        hwy = node['highway'] if 'highway' in node and not node['highway'] is None else ''
        G.add_node(node_id, osmid=node_id, lat=node['lat'], lon=node['lon'], highway=hwy)
    
    # add each osm way (aka, path) to the graph
    for path_id, path in paths.items():
        name = node['name'] if ('name' in node) and (not node['name'] is None) else ''
        city = node['city'] if ('city' in node) and (not node['city'] is None) else ''
        hwy = node['highway'] if ('highway' in node) and (not node['highway'] is None) else ''
        maxspeed = node['maxspeed'] if ('maxspeed' in node) and (not node['maxspeed'] is None) else ''
        G.add_path(path['nodes'], osmid=path_id, name=name, city=city, highway=hwy, maxspeed=maxspeed)
    
    # retain only the largest connected subgraph, if caller did not request retain_all
    G = get_largest_subgraph(G)
    
    # add length (great circle distance between vertices) attribute to each edge to use as weight
    G = add_edge_lengths(G)
    
    # change the node labels from osm ids to the standard sequential integers
    G = nx.convert_node_labels_to_integers(G)
    log('Created graph with {:,} nodes and {:,} edges in {:,.2f} seconds'.format(len(G.nodes()), len(G.edges()), time.time()-start_time))
    
    return G
    
    
def bbox_from_point(point, distance=1000):
    """
    Create a bounding box some distance in each direction from some lat-long point.
    
    Arguments:
    point = the lat-long point to create the bounding box around
    distance = how many meters the north, south, east, and west sides of the box should each be from the point
    
    Returns: tuple
    """
    north = vincenty(meters=distance).destination(point, bearing=0).latitude
    south = vincenty(meters=distance).destination(point, bearing=180).latitude
    east = vincenty(meters=distance).destination(point, bearing=90).longitude
    west = vincenty(meters=distance).destination(point, bearing=270).longitude
    log('Created bounding box {} meters in each direction from {}: {},{},{},{}'.format(distance, point, north, south, east, west))
    return north, south, east, west
    
    
def graph_from_bbox(north, south, east, west, truncate=True, name='unnamed'):
    """
    Create a networkx graph from OSM data within some bounding box.
    
    Arguments:
    north = northern latitude of bounding box
    south = southern latitude of bounding box
    east = eastern longitude of bounding box
    west = western longitude of bounding box
    truncate = if true, remove all nodes that lie outside the bounding box
    name = the name of the graph
    
    Returns: networkx graph
    """
    osm_data = osm_net_download(north, south, east, west)
    G = create_graph(osm_data, name=name)
    if truncate:
        G = truncate_graph_bbox(G, north, south, east, west)
    
    log('graph_from_bbox() returning graph with {:,} nodes and {:,} edges'.format(len(G.nodes()), len(G.edges())))
    return  G
    
    
def graph_from_point(center_point, distance=1000, distance_type='bbox', name='unnamed'):
    """
    Create a networkx graph from OSM data within some distance of some lat-long point.
    
    Arguments:
    center_point = the central point around which to construct the graph
    distance = retain only those nodes within this many meters of the center of the graph
    distance_type = if 'bbox', retain only those nodes within a bounding box of the distance parameter
                    if 'network', retain only those nodes within some network distance from the center-most node
    name = the name of the graph
    
    Returns: networkx graph
    """
    north, south, east, west = bbox_from_point(center_point, distance)
    if distance_type == 'bbox':
        G = graph_from_bbox(north, south, east, west, truncate=True, name=name)
    elif distance_type == 'network':
        G = graph_from_bbox(north, south, east, west, truncate=False, name=name)
        centermost_node = get_nearest_node(G, center_point)
        G = truncate_graph_dist(G, centermost_node, max_distance=distance)
    else:
        raise ValueError('distance_type must be "bbox" or "network"')
    
    log('graph_from_point() returning graph with {:,} nodes and {:,} edges'.format(len(G.nodes()), len(G.edges())))
    return G
        
        
def graph_from_address(address, distance=1000, distance_type='bbox', return_coords=False, name='unnamed', geocoder_timeout=30):
    """
    Create a networkx graph from OSM data within some distance of some address.
    
    Arguments:
    address = the address to geocode and use as the central point around which to construct the graph
    distance = retain only those nodes within this many meters of the center of the graph
    distance_type = if 'bbox', retain only those nodes within a bounding box of the distance parameter
                    if 'network', retain only those nodes within some network distance from the center-most node
    return_coords = optionally also return the geocoded coordinates of the address
    name = the name of the graph
    geocoder_timeout = how many seconds to wait for server response before the geocoder times-out
    
    Returns: networkx graph
    """
    geolocation = Nominatim().geocode(query=address, timeout=geocoder_timeout)
    point = (geolocation.latitude, geolocation.longitude)
    G = graph_from_point(point, distance, distance_type, name=name)
    log('graph_from_address() returning graph with {:,} nodes and {:,} edges'.format(len(G.nodes()), len(G.edges())))
    
    if return_coords:
        return G, point
    else:
        return G
        
        
def graph_from_place(query, retain_all=False, name='unnamed', which_result=1):
    """
    Create a networkx graph from OSM data within the spatial boundaries of some geocodable place(s).
    
    Arguments:
    query = a string or list of strings representing places to geocode/download data for
    retain_all = if True, return the entire graph even if it is not connected
    name = the name of the graph
    
    Returns: networkx graph
    """
    # create a GeoDataFrame with the spatial boundaries of the place(s)
    if isinstance(query, str):
        gdf_place = gdf_from_place(query, which_result=1)
        name = query
    elif isinstance(query, list):
        gdf_place = gdf_from_places(query)
    else:
        raise ValueError('query must be a string or a list of query strings')
    
    # get the bounding box containing the place(s) then get the graph within that bounding box
    north = gdf_place['bbox_north'].max()
    south = gdf_place['bbox_south'].min()
    east = gdf_place['bbox_east'].max()
    west = gdf_place['bbox_west'].min()
    G = graph_from_bbox(north, south, east, west, truncate=False, name=name)
    
    # truncate the graph to the shape of the place(s) polygon then return it
    polygon = gdf_place['geometry'].unary_union
    G = truncate_graph_polygon(G, polygon, retain_all=retain_all)
    log('graph_from_place() returning graph with {:,} nodes and {:,} edges'.format(len(G.nodes()), len(G.edges())))
    
    return G
    

def project_graph(G):
    """
    Project a graph from lat-long to UTM
    
    Arguments:
    G = the networkx graph to be projected
    
    Returns: networkx graph
    """
    # create a GeoDataFrame of the nodes, name it, convert osmid to str, and create a geometry column
    start_time = time.time()
    nodes = {node_id:data for node_id, data in G.nodes(data=True)}
    gdf = gpd.GeoDataFrame(nodes).T
    gdf.name = G.name
    gdf['osmid'] = gdf['osmid'].astype(str)
    gdf['geometry'] = gdf.apply(lambda row: Point(row['lon'], row['lat']), axis=1)
    log('Created a GeoDataFrame from graph in {:,.2f} seconds'.format(time.time()-start_time))
    
    # project the GeoDataFrame to UTM
    gdf_utm = project_utm(gdf)
    
    # extract projected x and y values from the geometry column
    start_time = time.time()
    gdf_utm['x'] = gdf_utm['geometry'].map(lambda point: point.x)
    gdf_utm['y'] = gdf_utm['geometry'].map(lambda point: point.y)
    gdf_utm = gdf_utm.drop('geometry', axis=1)
    log('Extracted projected geometry from GeoDataFrame in {:,.2f} seconds'.format(time.time()-start_time))
    
    # clear the graph to make it tabula rasa for the projected data
    start_time = time.time()
    edges = G.edges(data=True)
    G.clear()
    
    # add the projected nodes and all their attributes to the graph
    G.add_nodes_from(gdf_utm.index)
    attributes = gdf_utm.to_dict()
    for name in gdf_utm.columns:
        nx.set_node_attributes(G, name, attributes[name])
    
    # add the edges and all their attributes to the graph
    for u, v, attributes in edges:
        G.add_edge(u, v, attributes)
        
    log('Rebuilt projected graph in {:,.2f} seconds'.format(time.time()-start_time))    
    return G
    


def save_graph(G, filename='graph', data_folder=_data_folder):
    """
    Save graph as file to disk
    
    """
    # convert all the node attribute values to string or it won't save
    for node, data in G.nodes(data=True):
        for key in data:
            data[key] = str(data[key])
    nx.write_graphml(G, '{}/{}.graphml'.format(data_folder, filename))
    
    
def plot_graph(G, bbox=None, x='lon', y='lat', fig_height=6, fig_width=None,
               show=True, save=False, filename='temp.jpg', dpi=300,
               node_color='#66ccff', node_size=15, node_alpha=1, node_edgecolor='none',
               edge_color='#999999', edge_linewidth=1, edge_alpha=1):
    """
    Plot a networkx graph.
    
    Arguments:
    G = the networkx graph to plot
    bbox = a bbox tuple as north,south,east,west - if None will calculate from spatial extents of data
    x = node attribute to use as x coordinate
    y = node attribute to use as y coordinate
    
    Returns: matplotlib figure, axis    
    """
    
    log('Begin plotting the graph')
    node_Xs = [float(node[x]) for node in G.node.values()]
    node_Ys = [float(node[y]) for node in G.node.values()]
    
    if bbox is None:
        north = max(node_Ys)
        south = min(node_Ys)
        east = max(node_Xs)
        west = min(node_Xs)
    else:
        north, south, east, west = bbox
    bbox_aspect_ratio = (north-south)/(east-west)
    
    fig, ax = plt.subplots(figsize=(fig_height / bbox_aspect_ratio, fig_height))
    
    # draw the edges as lines from node to node
    start_time = time.time()
    lines = [((G.node[u][x],G.node[u][y]),(G.node[v][x],G.node[v][y])) for u,v in G.edges()]
    lc = mc.LineCollection(lines, colors=edge_color, linewidths=edge_linewidth, alpha=edge_alpha)
    ax.add_collection(lc)
    log('Drew the graph edges in {:,.2f} seconds'.format(time.time()-start_time))
    
    # scatter plot the nodes
    ax.scatter(node_Xs, node_Ys, s=node_size, c=node_color, alpha=node_alpha, edgecolor=node_edgecolor)
    
    # set the extent of the figure
    ax.set_ylim((south, north))
    ax.set_xlim((west, east))
    
    # save the figure if specified
    if save:
        start_time = time.time()
        if not os.path.exists(_imgs_folder):
            os.makedirs(_imgs_folder)
        path_filename = '{}/{}'.format(_imgs_folder, filename)
        fig.savefig(path_filename, dpi=dpi, bbox_inches='tight')
        log('Saved the figure to disk in {:,.2f} seconds'.format(time.time()-start_time))
    
    if show:
        start_time = time.time()
        plt.show()
        log('Showed the plot in {:,.2f} seconds'.format(time.time()-start_time))
    
    return fig, ax


def plot_graph_route(G, route, origin_point=None, destination_point=None, bbox=None, x='lon', y='lat', fig_height=6, fig_width=None,
                     show=True, save=False, filename='temp.jpg', dpi=300,
                     node_color='#999999', node_size=15, node_alpha=1, node_edgecolor='none',
                     edge_color='#999999', edge_linewidth=1, edge_alpha=1,
                     route_color='r', route_linewidth=4, route_alpha=0.5, orig_dest_node_alpha=0.5,
                     orig_dest_node_size=100, orig_dest_node_color='r', orig_dest_point_color='b'):
    
    # plot the graph but not the route
    fig, ax = plot_graph(G, bbox=bbox, x=x, y=y, fig_height=fig_height, fig_width=fig_width,
                         show=False, save=save, filename=filename, dpi=dpi,
                         node_color=node_color, node_size=node_size, node_alpha=node_alpha, node_edgecolor=node_edgecolor,
                         edge_color=edge_color, edge_linewidth=edge_linewidth, edge_alpha=edge_alpha)
    
    # get the lats and lons of each node along the route
    path_lats = [float(G.node[node][y]) for node in route]
    path_lons = [float(G.node[node][x]) for node in route]
    
    origin_node = route[0]
    destination_node = route[-1]
        
    if origin_point is None or destination_point is None:
        # if caller didn't pass points, use the first and last node in route as origin/destination    
        origin_destination_lats = (G.node[origin_node][y], G.node[destination_node][y])
        origin_destination_lons = (G.node[origin_node][x], G.node[destination_node][x])
    else:
        # otherwise, use the passed points as origin/destination
        origin_destination_lats = (origin_point[0], destination_point[0])
        origin_destination_lons = (origin_point[1], destination_point[1])
        orig_dest_node_color = orig_dest_point_color
    
    # scatter the origin and destination points then plot the route lines
    ax.scatter(origin_destination_lons, origin_destination_lats, s=orig_dest_node_size, 
               c=orig_dest_node_color, alpha=orig_dest_node_alpha, edgecolor=node_edgecolor)
    ax.plot(path_lons, path_lats, color=route_color, linewidth=route_linewidth, alpha=route_alpha)
    
    if show:
        start_time = time.time()
        plt.show()
        log('Showed the plot in {:,.2f} seconds'.format(time.time()-start_time))
        
    return fig, ax
    
    