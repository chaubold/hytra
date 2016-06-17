'''
Utilities that help with loading / saving as well as constructing and parsing
hypotheses graphs stored in our json (or python dictionary) format.
'''

import logging
import numpy as np
import commentjson as json

# ----------------------------------------------------------------------------
# Utility functions

def getLogger():
    ''' logger to be used in this module '''
    return logging.getLogger(__name__)

def readFromJSON(filename):
    ''' Read a dictionary from JSON '''
    with open(filename, 'r') as f:
        return json.load(f)

def writeToFormattedJSON(filename, dictionary):
    ''' Write a dictionary to JSON, but use proper readable formatting  '''
    with open(filename, 'w') as f:
        json.dump(dictionary, f, indent=4, separators=(',', ': '))

def getMappingsBetweenUUIDsAndTraxels(model):
    '''
    From a dictionary encoded model, load the "traxelToUniqueId" mapping,
    create a reverse mapping, and return both.
    '''

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

    return traxelIdPerTimestepToUniqueIdMap, uuidToTraxelMap

def getMergersDetectionsLinksDivisions(result, uuidToTraxelMap, withDivisions):
    # load results and map indices
    mergers = [timestepIdTuple + (entry['value'],) for entry in result['detectionResults'] if entry['value'] > 1 for timestepIdTuple in uuidToTraxelMap[int(entry['id'])]]
    detections = [timestepIdTuple for entry in result['detectionResults'] if entry['value'] > 0 for timestepIdTuple in uuidToTraxelMap[int(entry['id'])]]
    if withDivisions:
        divisions = [uuidToTraxelMap[int(entry['id'])][-1] for entry in result['divisionResults'] if entry['value'] == True]
    else:
        divisions = None
    links = [(uuidToTraxelMap[int(entry['src'])][-1], uuidToTraxelMap[int(entry['dest'])][0]) for entry in result['linkingResults'] if entry['value'] > 0]

    # add all internal links of tracklets
    for v in uuidToTraxelMap.values():
        prev = None
        for timestepIdTuple in v:
            if prev is not None:
                links.append((prev, timestepIdTuple))
            prev = timestepIdTuple

    return mergers, detections, links, divisions

def getMergersPerTimestep(mergers, timesteps):
    ''' returns mergersPerTimestep = { "<timestep>": {<idx>: <count>, <idx>: <count>, ...}, "<timestep>": {...}, ... } '''
    mergersPerTimestep = dict([(t, dict([(idx, count) for timestep, idx, count in mergers if timestep == int(t)])) for t in timesteps])
    return mergersPerTimestep

def getDetectionsPerTimestep(detections, timesteps):
    ''' returns detectionsPerTimestep = { "<timestep>": [<idx>, <idx>, ...], "<timestep>": [...], ...} '''
    detectionsPerTimestep = dict([(t, [idx for timestep, idx in detections if timestep == int(t)]) for t in timesteps])
    return detectionsPerTimestep
    
def getLinksPerTimestep(links, timesteps):
    ''' returns linksPerTimestep = { "<timestep>": [(<idxA> (at previous timestep), <idxB> (at timestep)), (<idxA>, <idxB>), ...], ...} '''
    linksPerTimestep = dict([(t, [(a[1], b[1]) for a, b in links if b[0] == int(t)]) for t in timesteps])
    return linksPerTimestep

def getMergerLinks(linksPerTimestep, mergersPerTimestep, timesteps):
    """ returns merger links as triplets [("timestep", (sourceIdAtTMinus1, destIdAtT)), (), ...]"""
    # filter links: at least one of the two incident nodes must be a merger 
    # for it to be added to the merger resolving graph
    mergerLinks = [(t,(a, b)) for t in timesteps for a, b in linksPerTimestep[t] if a in mergersPerTimestep[str(int(t)-1)] or b in mergersPerTimestep[t]]
    return mergerLinks

def getDivisionsPerTimestep(divisions, linksPerTimestep, timesteps, withDivisions):
    ''' returns divisionsPerTimestep = { "<timestep>": {<parentIdx>: [<childIdx>, <childIdx>], ...}, "<timestep>": {...}, ... } '''
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

    return divisionsPerTimestep

def negLog(features):
    fa = np.array(features)
    fa[fa < 0.0000000001] = 0.0000000001
    return list(np.log(fa) * -1.0)

def listify(l):
    return [[e] for e in l]

def check(feats):
    grad = feats[1:] - feats[0:-1]
    for i in range(len(grad) - 1):
        assert(grad[i+1] > grad[i])

def convexify(l, eps):
    features = np.array(l)
    if features.shape[1] != 1:
        raise ValueError('This script can only convexify feature vectors with one feature per state!')

    # Note from Numpy Docs: In case of multiple occurrences of the minimum values, the indices corresponding to the first occurrence are returned.
    bestState = np.argmin(features)

    for direction in [-1, 1]:
        pos = bestState + direction
        previousGradient = 0
        while pos >= 0 and pos < features.shape[0]:
            newGradient = features[pos] - features[pos-direction]
            if np.abs(newGradient - previousGradient) < eps:
                # cost function's derivative is roughly constant, add epsilon
                previousGradient += eps
                features[pos] = features[pos-direction] + previousGradient
            elif newGradient < previousGradient:
                # cost function got too flat, set feature value to match old slope
                previousGradient += eps
                features[pos] = features[pos-direction] + previousGradient
            else:
                # all good, continue with new slope
                previousGradient = newGradient

            pos += direction
    try:
        check(features)
    except:
        getLogger().warning("Failed convexifying {}".format(features))
    return listify(features.flatten())

