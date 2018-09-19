"""
NOTES:
- numSynapsesPerCore needs to count reused synapses!
"""
import numpy as np

from nxsdk.arch.n2a.graph.graph import N2Board


def setupNetwork():
    boardId = 1
    numChips = 1
    numCoresPerChip = [2]
    numSynapsesPerCore = [[0, 2*2]]
    board = N2Board(boardId, numChips, numCoresPerChip, numSynapsesPerCore)

    n2Core0 = board.n2Chips[0].n2Cores[0]
    n2Core1 = board.n2Chips[0].n2Cores[1]

    tauU = 10
    n2Core0.cxProfileCfg[0].configure(decayU=int(1/tauU*2**12))
    n2Core0.vthProfileCfg[0].staticCfg.configure(vth=40)
    n2Core0.numUpdates.configure(numUpdates=1)

    n2Core1.cxProfileCfg[0].configure(decayU=int(1/tauU*2**12))
    n2Core1.vthProfileCfg[0].staticCfg.configure(vth=40)
    n2Core1.numUpdates.configure(numUpdates=1)

    # make inputs spike
    bias = [10, 20]
    for i in range(2):
        n2Core0.cxCfg[i].configure(bias=bias[i], biasExp=6)
        phasex = 'phase%d' % (i % 4,)
        n2Core0.cxMetaState[i//4].configure(**{phasex: 2})

    # n2Core0.axonMap[0].configure(ptr=0, len=1)
    # n2Core0.axonMap[1].configure(ptr=1, len=1)

    # n2Core0.axonCfg[0].discrete.configure(coreId=n2Core1.id, axonId=0)
    # n2Core0.axonCfg[1].discrete.configure(coreId=n2Core1.id, axonId=1)

    # n2Core0.createDiscreteAxon(0, 0, n2Core1.id, 0)
    n2Core0.createDiscreteAxon(1, 0, n2Core1.id, 1)

    # set up output synapses
    for i in range(4):
        n2Core1.cxCfg[i].configure(bias=0, biasExp=0)

    # n2Core1.synapseMap[0].synapsePtr = 0
    # n2Core1.synapseMap[0].synapseLen = 2
    # n2Core1.synapseMap[0].discreteMapEntry.configure(cxBase=0)

    n2Core1.synapseMap[1].synapsePtr = 0
    n2Core1.synapseMap[1].synapseLen = 2
    # n2Core1.synapseMap[1].discreteMapEntry.configure(cxBase=0)
    n2Core1.synapseMap[1].discreteMapEntry.configure(cxBase=2)

    n2Core1.synapses[0].CIdx = 0
    n2Core1.synapses[0].Wgt = 4
    n2Core1.synapses[0].synFmtId = 1
    n2Core1.synapses[1].CIdx = 1
    n2Core1.synapses[1].Wgt = -4
    n2Core1.synapses[1].synFmtId = 1

    # Configure a synapseFormat
    n2Core1.synapseFmt[1].wgtBits = 7
    n2Core1.synapseFmt[1].numSynapses = 63
    n2Core1.synapseFmt[1].idxBits = 1
    n2Core1.synapseFmt[1].compression = 3
    n2Core1.synapseFmt[1].fanoutType = 1

    return board


def runNetwork(board, doPlot):
    n2Core0 = board.n2Chips[0].n2Cores[0]
    n2Core1 = board.n2Chips[0].n2Cores[1]

    mon = board.monitor

    uProbe0 = mon.probe(n2Core0.cxState, range(0, 2), 'u')
    vProbe0 = mon.probe(n2Core0.cxState, range(0, 2), 'v')

    uProbe1 = mon.probe(n2Core1.cxState, range(0, 4), 'u')
    vProbe1 = mon.probe(n2Core1.cxState, range(0, 4), 'v')

    numStepsPerRun = 11
    board.run(numStepsPerRun)

    print(np.column_stack([p.timeSeries.data for p in vProbe0]))
    print(np.column_stack([p.timeSeries.data for p in vProbe1]))


if __name__ == '__main__':
    board = setupNetwork()
    runNetwork(board, True)
