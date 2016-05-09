import commentjson as json
import logging
import os
import configargparse as argparse
import numpy as np
import h5py
import networkx as nx
from pluginsystem.plugin_manager import TrackingPluginManager

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Given a hypotheses json graph and a result.json, this script'
                        + ' resolves all mergers by updating the segmentation and inserting the appropriate '
                        + 'nodes and links.',
                        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-c', '--config', is_config_file=True, help='config file path', dest='config_file', required=True)

    parser.add_argument('--graph-json-file', required=True, type=str, dest='model_filename',
                        help='Filename of the json model description')
    parser.add_argument('--result-json-file', required=True, type=str, dest='result_filename',
                        help='Filename of the json file containing results')
    parser.add_argument('--label-image-filename', required=True, type=str, dest='label_image_filename',
                        help='Filename of the original ilasitk tracking project')
    parser.add_argument('--label-image-path', dest='label_image_path', type=str,
                        default='/TrackingFeatureExtraction/LabelImage/0000/[[%d, 0, 0, 0, 0], [%d, %d, %d, %d, 1]]',
                        help='internal hdf5 path to label image')
    parser.add_argument('--raw-data-file', type=str, dest='raw_filename', default=None,
                      help='filename to the raw h5 file')
    parser.add_argument('--raw-data-path', type=str, dest='raw_path', default='volume/data',
                      help='Path inside the raw h5 file to the data')
    parser.add_argument('--outModel', type=str, dest='out_model_filename', required=True, 
                        help='Filename of the json model containing the hypotheses graph including new nodes')
    parser.add_argument('--outLabelImage', type=str, dest='out_label_image', required=True, 
                        help='Filename where to store the label image with updated segmentation')
    parser.add_argument('--outResult', type=str, dest='out_result', required=True, 
                        help='Filename where to store the new result')
    
    args, _ = parser.parse_known_args()
    logging.basicConfig(level=logging.INFO)

    logging.basicConfig(level=logging.DEBUG)

    with open(args.model_filename, 'r') as f:
        model = json.load(f)

    with open(args.result_filename, 'r') as f:
        result = json.load(f)

    # create reverse mapping from json uuid to (timestep,ID)
    traxelIdPerTimestepToUniqueIdMap = model['traxelToUniqueId']
    timesteps = [t for t in traxelIdPerTimestepToUniqueIdMap.keys()]
    uuidToTraxelMap = {}
    for t in timesteps:
        for i in traxelIdPerTimestepToUniqueIdMap[t].keys():
            uuid = traxelIdPerTimestepToUniqueIdMap[t][i]
            if uuid not in uuidToTraxelMap:
                uuidToTraxelMap[uuid] = []
            uuidToTraxelMap[uuid].append((int(t), int(i)))

    # sort the list of traxels per UUID by their timesteps
    for v in uuidToTraxelMap.values():
        v.sort(key=lambda timestepIdTuple: timestepIdTuple[0])

    assert(result['detectionResults'] is not None)
    assert(result['linkingResults'] is not None)
    withDivisions = result['divisionResults'] is not None

    # load results and map indices
    mergers = [timestepIdTuple + (entry['value'],) for entry in result['detectionResults'] if entry['value'] > 1 for timestepIdTuple in uuidToTraxelMap[int(entry['id'])]]
    detections = [timestepIdTuple for entry in result['detectionResults'] if entry['value'] > 0 for timestepIdTuple in uuidToTraxelMap[int(entry['id'])]]
    if withDivisions:
        divisions = [uuidToTraxelMap[int(entry['id'])][-1] for entry in result['divisionResults'] if entry['value'] == True]
    links = [(uuidToTraxelMap[int(entry['src'])][-1], uuidToTraxelMap[int(entry['dest'])][0]) for entry in result['linkingResults'] if entry['value'] > 0]

    # add all internal links of tracklets
    for v in uuidToTraxelMap.values():
        prev = None
        for timestepIdTuple in v:
            if prev is not None:
                links.append((prev, timestepIdTuple))
            prev = timestepIdTuple

    # group by timestep for graph creation
    # mergersPerTimestep = { "<timestep>": {<idx>: <count>, <idx>: <count>, ...}, "<timestep>": {...}, ... }
    mergersPerTimestep = dict([(t, dict([(idx, count) for timestep, idx, count in mergers if timestep == int(t)])) for t in timesteps])
    # detectionsPerTimestep = { "<timestep>": [<idx>, <idx>, ...], "<timestep>": [...], ...}
    detectionsPerTimestep = dict([(t, [idx for timestep, idx in detections if timestep == int(t)]) for t in timesteps])
    # linksPerTimestep = { "<timestep>": [(<idxA> (at previous timestep), <idxB> (at timestep)), (<idxA>, <idxB>), ...], ...}
    linksPerTimestep = dict([(t, [(a[1], b[1]) for a, b in links if b[0] == int(t)]) for t in timesteps])

    # filter links: at least one of the two incident nodes must be a merger 
    # for it to be added to the merger resolving graph
    mergerLinks = [(t,(a, b)) for t in timesteps for a, b in linksPerTimestep[t] if a in mergersPerTimestep[str(int(t)-1)] or b in mergersPerTimestep[t]]

    # divisionsPerTimestep = { "<timestep>": {<parentIdx>: [<childIdx>, <childIdx>], ...}, "<timestep>": {...}, ... }
    if withDivisions:
        # find children of divisions by looking for the active links
        divisionsPerTimestep = {}
        for t in timesteps:
            divisionsPerTimestep[t] = {}
            for div_timestep, div_idx in divisions:
                if div_timestep == int(t) - 1:
                    # we have an active division of the mother cell "div_idx" in the previous frame
                    children = [b for a,b in linksPerTimestep[t] if a == div_idx]
                    assert(len(children) == 2)
                    divisionsPerTimestep[t][div_idx] = children
    else:
        divisionsPerTimestep = dict([(t,{}) for t in timesteps])

    # ------------------------------------------------------------
    # set up a networkx graph consisting of mergers (not resolved yet!) and their direct neighbors
    unresolvedGraph = nx.DiGraph()

    def source(timestep, link):
        return str(int(timestep) - 1), link[0]

    def target(timestep, link):
        return timestep, link[1]

    def addNode(node):
        ''' add a node to the unresolved graph and fill in the properties `division` and `count` '''
        timestep, idx = node
        division = idx in divisionsPerTimestep[timestep]
        count = 1
        if idx in mergersPerTimestep[timestep]:
            assert(not division)
            count = mergersPerTimestep[timestep][idx]
        unresolvedGraph.add_node(node, division=division, count=count)

    # add nodes
    for t,link in mergerLinks:
        for n in [source(t, link), target(t, link)]:
            if not unresolvedGraph.has_node(n):
                addNode(n)
        unresolvedGraph.add_edge(source(t, link), target(t,link))

    # now we split up the division nodes if they have two outgoing links
    resolvedGraph = unresolvedGraph.copy()
    numDivisionNodes = 0
    for n in unresolvedGraph:
        if unresolvedGraph.node[n]['division'] and len(unresolvedGraph.out_edges(n)) == 2:
            # create a duplicate node, make one link start from there
            duplicate = (n[0], 'div-{}'.format(numDivisionNodes))
            numDivisionNodes += 1
            resolvedGraph.add_node(duplicate, division=False, count=1)

            dest = unresolvedGraph.out_edges(n)[0]
            resolvedGraph.add_edge(duplicate, dest)
            resolvedGraph.remove_edge(n, dest)

            # store node references
            resolvedGraph.node[duplicate]['origin'] = n
            resolvedGraph.node[n]['duplicate'] = duplicate

    pluginManager = TrackingPluginManager(verbose=True)
    pluginManager.setImageProvider('LocalImageLoader')
    imageProvider = pluginManager.getImageProvider()
    mergerResolver = pluginManager.getMergerResolver()

    # store all raw and label images of the frames we need for merger resolving:
    labelImages = {}
    rawImages = {}

    # ------------------------------------------------------------
    # update segmentation of mergers from first timeframe to last and create new nodes:
    intTimesteps = [int(t) for t in timesteps]
    intTimesteps.sort()

    for intT in intTimesteps:
        t = str(intT)
        # use image provider plugin to load labelimage
        print args.label_image_filename, args.label_image_path, int(t)
        labelImage = imageProvider.getLabelImageForFrame(
            args.label_image_filename, args.label_image_path, int(t))
        labelImages[t] = labelImage
        nextObjectId = labelImage.max() + 1

        for idx in detectionsPerTimestep[t]:
            node = (t, idx)
            if node not in resolvedGraph:
                continue

            count = 1
            if idx in mergersPerTimestep[t]:
                count = mergersPerTimestep[t][idx]
            print("Looking at node {} in timestep {} with count {}".format(idx, t, count))
            
            # collect initializations from incoming
            initializations = []
            for predecessor, _ in unresolvedGraph.in_edges(node):
                initializations.extend(unresolvedGraph.node[predecessor]['fits'])
            # TODO: what shall we do if e.g. a 2-merger and a single object merge to 2 + 1, 
            # so there are 3 initializations for the 2-merger, and two initializations for the 1 merger?
            # What does pgmlink do in that case?

            # use merger resolving plugin to fit `count` objects, also updates labelimage!
            fittedObjects = mergerResolver.resolveMerger(labelImage, idx, nextObjectId, count, initializations)
            assert(len(fittedObjects) == count)

            # split up node if count > 1, duplicate incoming and outgoing arcs
            if count > 1:
                for idx, fit in zip(range(nextObjectId, nextObjectId + count), fittedObjects):
                    newNode = (t, idx)
                    resolvedGraph.add_node(newNode, division=False, count=1, origin=node)
                    
                    for e in unresolvedGraph.out_edges(node):
                        resolvedGraph.add_edge(newNode, e[1])
                    for e in unresolvedGraph.in_edges(node):
                        resolvedGraph.add_edge(e[0], newNode)

                resolvedGraph.remove_node(node)

            # each unresolved node stores its fitted shape(s) to be used 
            # as initialization in the next frame, this way division duplicates 
            # and de-merged nodes in the resolved graph do not need to store a fit as well
            unresolvedGraph.node[node]['fits'] = fittedObjects

    # import matplotlib.pyplot as plt
    # nx.draw_networkx(resolvedGraph)
    # plt.savefig("/Users/chaubold/test.pdf")

    # ------------------------------------------------------------
    # compute new object features and edge costs!
    print("Loading raw data")
    for t in labelImages.keys():
        rawImages[t] = imageProvider.getImageDataAtTimeFrame(
            args.raw_filename, args.raw_path, int(t))

    print("Computing object features")
    objectFeatures = {}
    imageShape = pluginManager.getImageProvider().getImageShape(args.label_image_filename, args.label_image_path)
    print("Found image of shape", imageShape)
    ndims = len(np.array(imageShape).squeeze()) - 1 # get rid of axes with length 1, and minus time axis
    print("Data has dimensionality ", ndims)
    for node in resolvedGraph.nodes_iter():
        t, idx = node
        # mask out this object only and compute features
        mask = labelImages[t].copy()
        mask[mask != idx] = 0
        mask[mask == idx] = 1
        
        # compute features, transform to one dict for frame
        frameFeatureDicts, ignoreNames = pluginManager.applyObjectFeatureComputationPlugins(
            ndims, rawImages[t], labelImages[t], int(t), args.raw_filename)
        frameFeatureItems = []
        for f in frameFeatureDicts:
            frameFeatureItems = frameFeatureItems + f.items()
        frameFeatures = dict(frameFeatureItems)

        # extract all features for this one object
        objectFeatureDict = {}
        for k, v in frameFeatures.iteritems():
            if k in ignoreNames:
                continue
            elif 'Polygon' in k:
                objectFeatureDict[k] = v[1]
            else:
                objectFeatureDict[k] = v[1, ...]
        objectFeatures[node] = objectFeatureDict

    print("Updating edge energy")
    for edge in resolvedGraph.edges_iter():
        featuresAtSrc = objectFeatures[edge[0]]
        featuresAtDest = objectFeatures[edge[1]]
        # pluginManager.applyTransitionFeatureVectorConstructionPlugins(
        #     featuresAtSrc, featuresAtDest, selectedFeatures)
        # TODO: predict or use distance
        transitionEnergy = 0.5
        resolvedGraph.edge[edge[0]][edge[1]]['energy'] = transitionEnergy
        resolvedGraph.edge[edge[0]][edge[1]]['capacity'] = 1

    # ------------------------------------------------------------
    # run min-cost max-flow to find merger assignments
    print("Running min-cost max-flow to find resolved merger assignments")
    # add source and sink
    resolvedGraph.add_node('source')
    resolvedGraph.add_node('sink')

    # connect nodes without incoming arcs to source, without outgoing to sink
    for node in resolvedGraph.nodes(): # cannot use iterator here as the graph changes in the loop!
        if len(resolvedGraph.in_edges(node)) == 0:
            resolvedGraph.add_edge('source', node, energy=0, capacity=1)
        elif len(resolvedGraph.out_edges(node)) == 0:
            resolvedGraph.add_edge(node, 'sink', energy=0, capacity=1)

    # find min-cost max-flow
    flowDict = nx.max_flow_min_cost(resolvedGraph, 'source', 'sink', capacity='capacity', weight='energy')
    mcmfAmount = sum(flowDict['source'][e[1]] for e in resolvedGraph.out_edges('source'))
    print("MinCostMaxFlow found flow: ", mcmfAmount)

    # ------------------------------------------------------------
    # TODO: fuse results back into original solution

