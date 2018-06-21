from __future__ import division

import numpy as np

# from nxsdk.arch.n2a.graph.graph import N2Board
# from nxsdk.arch.n2a.graph.inputgen import BasicSpikeGenerator

from nengo_loihi.allocators import one_to_one_allocator
from nengo_loihi.loihi_api import (
    CX_PROFILES_MAX, VTH_PROFILES_MAX, bias_to_manexp)


def build_board(board):
    from nxsdk.arch.n2a.graph.graph import N2Board

    n_chips = board.n_chips()
    n_cores_per_chip = board.n_cores_per_chip()
    n_synapses_per_core = board.n_synapses_per_core()

    n2board = N2Board(
        board.board_id, n_chips, n_cores_per_chip, n_synapses_per_core)

    assert len(board.chips) == len(n2board.n2Chips)
    for chip, n2chip in zip(board.chips, n2board.n2Chips):
        build_chip(n2chip, chip)

    return n2board


def build_chip(n2chip, chip):
    assert len(chip.cores) == len(n2chip.n2Cores)
    for core, n2core in zip(chip.cores, n2chip.n2Cores):
        build_core(n2core, core)


def build_core(n2core, core):
    assert len(core.cxProfiles) < CX_PROFILES_MAX
    assert len(core.vthProfiles) < VTH_PROFILES_MAX

    for i, cxProfile in enumerate(core.cxProfiles):
        n2core.cxProfileCfg[i].configure(
            decayV=cxProfile.decayV,
            decayU=cxProfile.decayU,
            refractDelay=cxProfile.refractDelay,
            bapAction=1,
        )

    for i, vthProfile in enumerate(core.vthProfiles):
        n2core.vthProfileCfg[i].staticCfg.configure(
            vth=vthProfile.vth,
        )

    for i, synapseFmt in enumerate(core.synapseFmts):
        if synapseFmt is None:
            continue

        n2core.synapseFmt[i].wgtLimitMant = synapseFmt.wgtLimitMant
        n2core.synapseFmt[i].wgtLimitExp = synapseFmt.wgtLimitExp
        n2core.synapseFmt[i].wgtExp = synapseFmt.wgtExp
        n2core.synapseFmt[i].discMaxWgt = synapseFmt.discMaxWgt
        n2core.synapseFmt[i].learningCfg = synapseFmt.learningCfg
        n2core.synapseFmt[i].tagBits = synapseFmt.tagBits
        n2core.synapseFmt[i].dlyBits = synapseFmt.dlyBits
        n2core.synapseFmt[i].wgtBits = synapseFmt.wgtBits
        n2core.synapseFmt[i].reuseSynData = synapseFmt.reuseSynData
        n2core.synapseFmt[i].numSynapses = synapseFmt.numSynapses
        n2core.synapseFmt[i].cIdxOffset = synapseFmt.cIdxOffset
        n2core.synapseFmt[i].cIdxMult = synapseFmt.cIdxMult
        n2core.synapseFmt[i].skipBits = synapseFmt.skipBits
        n2core.synapseFmt[i].idxBits = synapseFmt.idxBits
        n2core.synapseFmt[i].synType = synapseFmt.synType
        n2core.synapseFmt[i].fanoutType = synapseFmt.fanoutType
        n2core.synapseFmt[i].compression = synapseFmt.compression
        n2core.synapseFmt[i].stdpProfile = synapseFmt.stdpProfile
        n2core.synapseFmt[i].ignoreDly = synapseFmt.ignoreDly

    # TODO: allocator should be checking that vmin, vmax are the same
    #   for all groups on a core
    vmin, vmax = core.groups[0].vmin, core.groups[0].vmax
    assert all(group.vmin == vmin for group in core.groups)
    assert all(group.vmax == vmax for group in core.groups)
    negVmLimit = np.log2(-vmin + 1)
    posVmLimit = (np.log2(vmax + 1) - 9) * 0.5
    assert int(negVmLimit) == negVmLimit
    assert int(posVmLimit) == posVmLimit

    n2core.dendriteSharedCfg.configure(
        posVmLimit=int(posVmLimit),
        negVmLimit=int(negVmLimit))

    n2core.dendriteAccumCfg.configure(
        delayBits=3)
    # ^ DelayBits=3 allows 1024 Cxs per core

    n_cx = 0
    for group, cx_idxs, ax_range in core.iterate_groups():
        build_group(n2core, core, group, cx_idxs, ax_range)
        n_cx = max(max(cx_idxs), n_cx)

    n2core.numUpdates.configure(numUpdates=n_cx//4 + 1)


def build_group(n2core, core, group, cx_idxs, ax_range):
    assert group.scaleU is False
    assert group.scaleV is False

    print("Building %s on core.id=%d" % (group, n2core.id))

    for i, bias in enumerate(group.bias):
        bman, bexp = bias_to_manexp(bias)
        icx = core.cxProfileIdxs[group][i]
        ivth = core.vthProfileIdxs[group][i]

        ii = cx_idxs[i]
        n2core.cxCfg[ii].configure(
            bias=bman, biasExp=bexp, vthProfile=ivth, cxProfile=icx)

        phasex = 'phase%d' % (ii % 4,)
        n2core.cxMetaState[ii//4].configure(**{phasex: 2})

    for synapses in group.synapses:
        build_synapses(n2core, core, group, synapses, cx_idxs)

    for axons in group.axons:
        build_axons(n2core, core, group, axons, cx_idxs)

    for probe in group.probes:
        build_probe(n2core, core, group, probe, cx_idxs)


def build_synapses(n2core, core, group, synapses, cx_idxs):
    a0, a1 = core.synapse_axons[synapses]
    assert (a1 - a0) == len(synapses.weights)

    synapse_fmt_idx = core.synapse_fmt_idxs[synapses]

    s0 = core.synapse_entries[synapses][0]
    for a in range(a1 - a0):
        wa = synapses.weights[a] // synapses.synapse_fmt.scale
        ia = synapses.indices[a]
        assert len(wa) == len(ia)

        assert np.all(wa <= 255) and np.all(wa >= -256), str(wa)
        for k, (w, i) in enumerate(zip(wa, ia)):
            n2core.synapses[s0+k].CIdx = cx_idxs[i]
            n2core.synapses[s0+k].Wgt = w
            n2core.synapses[s0+k].synFmtId = synapse_fmt_idx

        n2core.synapseMap[a0+a].synapsePtr = s0
        n2core.synapseMap[a0+a].synapseLen = len(wa)
        n2core.synapseMap[a0+a].discreteMapEntry.configure()

        s0 += len(wa)


def build_axons(n2core, core, group, axons, cx_idxs):
    tchip_idx, tcore_idx, t0, t1 = core.board.find_synapses(axons.target)
    taxon_idxs = np.arange(t0, t1, dtype=np.int32)[axons.target_inds]
    n2board = n2core.parent.parent
    tchip_id = n2board.n2Chips[tchip_idx].id
    tcore_id = n2board.n2Chips[tchip_idx].n2Cores[tcore_idx].id
    assert axons.n_axons == len(cx_idxs)
    for i in range(axons.n_axons):
        n2core.createDiscreteAxon(
            cx_idxs[i], tchip_id, tcore_id, taxon_idxs[i])


def build_probe(n2core, core, group, probe, cx_idxs):
    assert probe.key in ('u', 'v', 's')
    key_map = {'s': 'spike'}
    key = key_map.get(probe.key, probe.key)

    n2board = n2core.parent.parent
    r = cx_idxs[probe.slice]
    p = n2board.monitor.probe(n2core.cxState, r, key)
    core.board.map_probe(probe, p)


class LoihiSimulator(object):
    def __init__(self, cx_model):
        self.build(cx_model)

        self._probe_filters = {}
        self._probe_filter_pos = {}

    def build(self, cx_model):
        self.model = cx_model

        # --- allocate
        allocator = one_to_one_allocator
        self.board = allocator(self.model)

        # --- build
        self.n2board = build_board(self.board)

    def print_cores(self):
        for j, n2chip in enumerate(self.n2board.n2Chips):
            print("Chip %d, id=%d" % (j, n2chip.id))
            for k, n2core in enumerate(n2chip.n2Cores):
                print("  Core %d, id=%d" % (k, n2core.id))

    def run_steps(self, steps):
        self.n2board.run(steps)

    def _filter_probe(self, cx_probe, data):
        dt = self.model.dt
        i = self._probe_filter_pos.get(cx_probe, 0)
        if i == 0:
            shape = data[0].shape
            synapse = cx_probe.synapse
            rng = None
            step = (synapse.make_step(shape, shape, dt, rng, dtype=data.dtype)
                    if synapse is not None else None)
            self._probe_filters[cx_probe] = step
        else:
            step = self._probe_filters[cx_probe]

        if step is None:
            self._probe_filter_pos[cx_probe] = i + len(data)
            return data
        else:
            filt_data = np.zeros_like(data)
            for k, x in enumerate(data):
                filt_data[k] = step((i + k) * dt, x)

            self._probe_filter_pos[cx_probe] = i + k
            return filt_data

    def get_probe_output(self, probe):
        cx_probe = self.model.objs[probe]['out']
        n2probe = self.board.probe_map[cx_probe]
        x = np.column_stack([p.timeSeries.data for p in n2probe])
        x = x if cx_probe.weights is None else np.dot(x, cx_probe.weights)
        return self._filter_probe(cx_probe, x)
