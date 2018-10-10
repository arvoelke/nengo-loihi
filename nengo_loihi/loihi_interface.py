from __future__ import division

from distutils.version import LooseVersion
import logging
import os
import sys
import time
import warnings

import jinja2
import numpy as np

from nengo.exceptions import SimulationError
from nengo.utils.stdlib import groupby

try:
    import nxsdk
    from nxsdk.arch.n2a.compiler.tracecfggen.tracecfggen import TraceCfgGen
    from nxsdk.arch.n2a.graph.graph import N2Board
    from nxsdk.arch.n2a.graph.inputgen import BasicSpikeGenerator
    from nxsdk.arch.n2a.graph.probes import N2SpikeProbe

except ImportError:
    exc_info = sys.exc_info()

    def no_nxsdk(*args, **kwargs):
        raise exc_info[1]
    nxsdk = N2Board = BasicSpikeGenerator = TraceCfgGen = N2SpikeProbe = (
        no_nxsdk)

import nengo_loihi.loihi_cx as loihi_cx
from nengo_loihi.allocators import one_to_one_allocator
from nengo_loihi.loihi_api import (
    CX_PROFILES_MAX, VTH_PROFILES_MAX, SpikeInput, bias_to_manexp)

logger = logging.getLogger(__name__)


def build_board(board):
    n_chips = board.n_chips()
    n_cores_per_chip = board.n_cores_per_chip()
    n_synapses_per_core = board.n_synapses_per_core()

    n2board = N2Board(
        board.board_id, n_chips, n_cores_per_chip, n_synapses_per_core)

    # add our own attribute for storing our spike generator
    n2board.global_spike_generator = BasicSpikeGenerator(n2board)

    assert len(board.chips) == len(n2board.n2Chips)
    for chip, n2chip in zip(board.chips, n2board.n2Chips):
        logger.debug("Building chip %s", chip)
        build_chip(n2chip, chip)

    return n2board


def build_chip(n2chip, chip):
    assert len(chip.cores) == len(n2chip.n2Cores)
    for core, n2core in zip(chip.cores, n2chip.n2Cores):
        logger.debug("Building core %s", core)
        build_core(n2core, core)


def build_core(n2core, core):  # noqa: C901
    assert len(core.cxProfiles) < CX_PROFILES_MAX
    assert len(core.vthProfiles) < VTH_PROFILES_MAX

    logger.debug("- Configuring cxProfiles")
    for i, cxProfile in enumerate(core.cxProfiles):
        n2core.cxProfileCfg[i].configure(
            decayV=cxProfile.decayV,
            decayU=cxProfile.decayU,
            refractDelay=cxProfile.refractDelay,
            enableNoise=cxProfile.enableNoise,
            bapAction=1,
        )

    logger.debug("- Configuring vthProfiles")
    for i, vthProfile in enumerate(core.vthProfiles):
        n2core.vthProfileCfg[i].staticCfg.configure(
            vth=vthProfile.vth,
        )

    logger.debug("- Configuring synapseFmts")
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

    logger.debug("- Configuring stdpPreCfgs")
    for i, traceCfg in enumerate(core.stdpPreCfgs):
        tcg = TraceCfgGen()
        tc = tcg.genTraceCfg(
            tau=traceCfg.tau,
            spikeLevelInt=traceCfg.spikeLevelInt,
            spikeLevelFrac=traceCfg.spikeLevelFrac,
        )
        tc.writeToRegister(n2core.stdpPreCfg[i])

    # --- learning
    firstLearningIndex = None
    for synapse in core.iterate_synapses():
        if synapse.tracing and firstLearningIndex is None:
            firstLearningIndex = core.synapse_axons[synapse][0]
            core.learning_coreid = n2core.id
            break

    numStdp = 0
    if firstLearningIndex is not None:
        for synapse in core.iterate_synapses():
            axons = np.array(core.synapse_axons[synapse])
            if synapse.tracing:
                numStdp += len(axons)
                assert np.all(len(axons) >= firstLearningIndex)
            else:
                assert np.all(len(axons) < firstLearningIndex)

    if numStdp > 0:
        logger.debug("- Configuring PES learning")
        # add configurations tailored to PES learning
        n2core.stdpCfg.configure(
            firstLearningIndex=firstLearningIndex,
            numRewardAxons=0,
        )

        assert core.stdp_pre_profile_idx is None
        assert core.stdp_profile_idx is None
        core.stdp_pre_profile_idx = 0  # hard-code for now
        core.stdp_profile_idx = 0  # hard-code for now (also in synapse_fmt)
        n2core.stdpPreProfileCfg[0].configure(
            updateAlways=1,
            numTraces=0,
            numTraceHist=0,
            stdpProfile=0,
        )

        # stdpProfileCfg positive error
        n2core.stdpProfileCfg[0].configure(
            uCodePtr=0,
            decimateExp=0,
            numProducts=1,
            requireY=1,
            usesXepoch=1,
        )
        n2core.stdpUcodeMem[0].word = 0x00102108  # 2^-7 learn rate

        # stdpProfileCfg negative error
        n2core.stdpProfileCfg[1].configure(
            uCodePtr=1,
            decimateExp=0,
            numProducts=1,
            requireY=1,
            usesXepoch=1,
        )
        n2core.stdpUcodeMem[1].word = 0x00f02108  # 2^-7 learn rate

        tcg = TraceCfgGen()
        tc = tcg.genTraceCfg(
            tau=0,
            spikeLevelInt=0,
            spikeLevelFrac=0,
        )
        tc.writeToRegister(n2core.stdpPostCfg[0])

    # TODO: allocator should be checking that vmin, vmax are the same
    #   for all groups on a core
    n_cx = 0
    if len(core.groups) > 0:
        group0 = core.groups[0]
        vmin, vmax = group0.vmin, group0.vmax
        assert all(group.vmin == vmin for group in core.groups)
        assert all(group.vmax == vmax for group in core.groups)
        negVmLimit = np.log2(-vmin + 1)
        posVmLimit = (np.log2(vmax + 1) - 9) * 0.5
        assert int(negVmLimit) == negVmLimit
        assert int(posVmLimit) == posVmLimit

        noiseExp0 = group0.noiseExp0
        noiseMantOffset0 = group0.noiseMantOffset0
        noiseAtDendOrVm = group0.noiseAtDendOrVm
        assert all(group.noiseExp0 == noiseExp0 for group in core.groups)
        assert all(group.noiseMantOffset0 == noiseMantOffset0
                   for group in core.groups)
        assert all(group.noiseAtDendOrVm == noiseAtDendOrVm
                   for group in core.groups)

        n2core.dendriteSharedCfg.configure(
            posVmLimit=int(posVmLimit),
            negVmLimit=int(negVmLimit),
            noiseExp0=noiseExp0,
            noiseMantOffset0=noiseMantOffset0,
            noiseAtDendOrVm=noiseAtDendOrVm,
        )

        n2core.dendriteAccumCfg.configure(
            delayBits=3)
        # ^ DelayBits=3 allows 1024 Cxs per core

        for group, cx_idxs, ax_range in core.iterate_groups():
            build_group(n2core, core, group, cx_idxs, ax_range)
            n_cx = max(max(cx_idxs) + 1, n_cx)

    for inp, cx_idxs in core.iterate_inputs():
        build_input(n2core, core, inp, cx_idxs)

    logger.debug("- Configuring numUpdates=%d", n_cx // 4 + 1)
    n2core.numUpdates.configure(
        numUpdates=n_cx // 4 + 1,
        numStdp=numStdp,
    )

    n2core.dendriteTimeState[0].tepoch = 2
    n2core.timeState[0].tepoch = 2


def build_group(n2core, core, group, cx_idxs, ax_range):
    assert group.scaleU is False
    assert group.scaleV is False

    logger.debug("Building %s on core.id=%d", group, n2core.id)

    for i, bias in enumerate(group.bias):
        bman, bexp = bias_to_manexp(bias)
        icx = core.cx_profile_idxs[group][i]
        ivth = core.vth_profile_idxs[group][i]

        ii = cx_idxs[i]
        n2core.cxCfg[ii].configure(
            bias=bman, biasExp=bexp, vthProfile=ivth, cxProfile=icx)

        phasex = 'phase%d' % (ii % 4,)
        n2core.cxMetaState[ii // 4].configure(**{phasex: 2})

    logger.debug("- Building %d synapses", len(group.synapses))
    for synapses in group.synapses:
        build_synapses(n2core, core, group, synapses, cx_idxs)

    logger.debug("- Building %d axons", len(group.axons))
    all_axons = []  # (cx, atom, type, tchip_id, tcore_id, taxon_id)
    for axons in group.axons:
        all_axons.extend(collect_axons(n2core, core, group, axons, cx_idxs))

    build_axons(n2core, core, group, all_axons)

    logger.debug("- Building %d probes", len(group.probes))
    for probe in group.probes:
        build_probe(n2core, core, group, probe, cx_idxs)


def build_input(n2core, core, spike_input, cx_idxs):
    assert len(spike_input.axons) > 0

    for probe in spike_input.probes:
        build_probe(n2core, core, spike_input, probe, cx_idxs)

    n2board = n2core.parent.parent

    assert isinstance(spike_input, loihi_cx.CxSpikeInput)
    loihi_input = SpikeInput(spike_input, core)
    loihi_input.set_axons(n2board)
    spike_input.set_loihi_input(loihi_input)

    # add any pre-existing spikes to spikegen
    spikes = loihi_input.collect_all_spikes()
    for spike in spikes:
        n2board.global_spike_generator.addSpike(
            time=spike.time, chipId=spike.chip_id,
            coreId=spike.core_id, axonId=spike.axon_id)


def build_synapses(n2core, core, group, synapses, cx_idxs):
    axon_ids = core.synapse_axons[synapses]
    # assert len(syn_idxs) == len(synapses.weights)

    synapse_fmt_idx = core.synapse_fmt_idxs[synapses]
    stdp_pre_cfg_idx = core.stdp_pre_cfg_idxs[synapses]

    atom_bits = synapses.atom_bits()
    axon_bits = synapses.axon_bits()
    atom_bits_extra = synapses.atom_bits_extra()

    target_cxs = set()
    synapse_map = {}  # map weight_idx to (ptr, pop_size, len)
    total_synapse_ptr = int(core.synapse_entries[synapses][0])
    for axon_idx, axon_id in enumerate(axon_ids):
        assert axon_id <= 2**axon_bits

        weight_idx = int(synapses.axon_weight_idx(axon_idx))
        cx_base = synapses.axon_cx_base(axon_idx)

        if weight_idx not in synapse_map:
            weights = synapses.weights[weight_idx]
            indices = synapses.indices[weight_idx]
            weights = weights // synapses.synapse_fmt.scale
            assert weights.ndim == 2
            assert weights.shape == indices.shape
            assert np.all(weights <= 255) and np.all(weights >= -256), str(weights)
            n_populations, n_cxs = weights.shape

            synapse_map[weight_idx] = (
                total_synapse_ptr, n_populations, n_cxs)

            for p in range(n_populations):
                for q in range(n_cxs):
                    cx_idx = cx_idxs[indices[p, q]]
                    n2core.synapses[total_synapse_ptr].configure(
                        CIdx=cx_idx,
                        Wgt=weights[p, q],
                        synFmtId=synapse_fmt_idx,
                        LrnEn=int(synapses.tracing),
                    )
                    target_cxs.add(cx_idx)
                    total_synapse_ptr += 1

        synapse_ptr, n_populations, n_cxs = synapse_map[weight_idx]
        assert n_populations <= 2**atom_bits

        if cx_base is None:
            # this is a dummy axon with no weights, so set n_cxs to 0
            synapse_ptr = 0
            n_cxs = 0
            cx_base = 0
        else:
            cx_base = int(cx_base)

        assert cx_base <= 256, "Currently limited by hardware"
        n2core.synapseMap[axon_id].synapsePtr = synapse_ptr
        n2core.synapseMap[axon_id].synapseLen = n_cxs
        if synapses.pop_type == 0:  # discrete
            assert n_populations == 1
            n2core.synapseMap[axon_id].discreteMapEntry.configure(
                cxBase=cx_base)
        elif synapses.pop_type == 16:  # pop16
            n2core.synapseMap[axon_id].popSize = n_populations
            assert cx_base % 4 == 0
            n2core.synapseMap[axon_id].population16MapEntry.configure(
                cxBase=cx_base//4, atomBits=atom_bits_extra)
        elif synapses.pop_type == 32:  # pop32
            n2core.synapseMap[axon_id].popSize = n_populations
            n2core.synapseMap[axon_id].population32MapEntry.configure(
                cxBase=cx_base)
        else:
            raise ValueError("Unrecognized pop_type: %d" % (synapses.pop_type))

        if synapses.tracing:
            assert core.stdp_pre_profile_idx is not None
            assert stdp_pre_cfg_idx is not None
            n2core.synapseMap[axon_id+1].singleTraceEntry.configure(
                preProfile=core.stdp_pre_profile_idx, tcs=stdp_pre_cfg_idx)

    assert total_synapse_ptr == core.synapse_entries[synapses][1], (
        "Synapse pointer did not align with precomputed synapses length")

    if synapses.tracing:
        assert core.stdp_profile_idx is not None
        for target_cx in target_cxs:
            # TODO: check that no cx gets configured by multiple synapses
            n2core.stdpPostState[target_cx].configure(
                stdpProfile=core.stdp_profile_idx,
                traceProfile=3,  # TODO: why this value
            )


def collect_axons(n2core, core, group, axons, cx_ids):
    synapses = axons.target
    tchip_idx, tcore_idx, tsyn_idxs = core.board.find_synapses(synapses)
    n2board = n2core.parent.parent
    tchip_id = n2board.n2Chips[tchip_idx].id
    tcore_id = n2board.n2Chips[tchip_idx].n2Cores[tcore_idx].id

    cx_idxs = np.arange(len(cx_ids))
    spikes = axons.map_cx_spikes(cx_idxs)

    all_axons = []  # (cx, atom, type, tchip_id, tcore_id, taxon_id)
    for cx_id, spike in zip(cx_ids, spikes):
        taxon_idx = int(spike.axon_id)
        taxon_id = int(tsyn_idxs[taxon_idx])
        atom = int(spike.atom)
        n_populations = synapses.axon_populations(taxon_idx)
        all_axons.append((cx_id, atom, synapses.pop_type,
                          tchip_id, tcore_id, taxon_id))
        if synapses.pop_type == 0:  # discrete
            assert atom == 0
            assert n_populations == 1
        elif synapses.pop_type == 16:  # pop16
            assert len(core.groups) == 0 or (len(core.groups) == 1 and
                                             group is core.groups[0])
            assert len(group.probes) == 0
        elif synapses.pop_type == 32:  # pop32
            assert len(core.groups) == 0 or (len(core.groups) == 1 and
                                             group is core.groups[0])
            assert len(group.probes) == 0
        else:
            raise ValueError("Unrecognized pop_type: %d" % (synapses.pop_type))

    return all_axons


def build_axons(n2core, core, group, all_axons):
    if len(all_axons) == 0:
        return

    pop_type0 = all_axons[0][2]
    if pop_type0 == 0:
        for cx_id, atom, pop_type, tchip_id, tcore_id, taxon_id in all_axons:
            assert pop_type == 0, "All axons must be discrete, or none"
            assert atom == 0
            n2core.createDiscreteAxon(
                srcCxId=cx_id,
                dstChipId=tchip_id, dstCoreId=tcore_id, dstSynMapId=taxon_id)

        return
    else:
        assert all(axon[2] != 0 for axon in all_axons), (
            "All axons must be discrete, or none")

    axons_by_cx = groupby(all_axons, key=lambda x: x[0])  # group by cx_id

    axon_id = 0
    axon_map = {}
    for cx_id, cx_axons in axons_by_cx:
        if len(cx_axons) == 0:
            continue

        # cx_axon -> (cx, atom, type, tchip_id, tcore_id, taxon_id)
        assert all(cx_axon[0] == cx_id for cx_axon in cx_axons)
        atom = cx_axons[0][1]
        assert all(cx_axon[1] == atom for cx_axon in cx_axons), (
            "cx atom must be the same for all axons")

        cx_axons = sorted(cx_axons, key=lambda a: a[2:])
        key = tuple(cx_axon[2:] for cx_axon in cx_axons)
        if key not in axon_map:
            axon_id0 = axon_id
            axon_len = 0

            for cx_axon in cx_axons:
                pop_type, tchip_id, tcore_id, taxon_id = cx_axon[2:]
                if pop_type == 0:  # discrete
                    assert False, "Should have been handled in code above"
                elif pop_type == 16:  # pop16
                    n2core.axonCfg[axon_id].pop16.configure(
                        coreId=tcore_id, axonId=taxon_id)
                    axon_id += 1
                    axon_len += 1
                elif pop_type == 32:  # pop32
                    n2core.axonCfg[axon_id].pop32_0.configure(
                        coreId=tcore_id, axonId=taxon_id)
                    n2core.axonCfg[axon_id+1].pop32_1.configure()
                    axon_id += 2
                    axon_len += 2
                else:
                    raise ValueError("Unrecognized pop_type: %d" % (pop_type,))

            axon_map[key] = (axon_id0, axon_len)

        axon_ptr, axon_len = axon_map[key]
        n2core.axonMap[cx_id].configure(ptr=axon_ptr, len=axon_len, atom=atom)


def build_probe(n2core, core, group, probe, cx_idxs):
    assert probe.key in ('u', 'v', 's')
    key_map = {'s': 'spike'}
    key = key_map.get(probe.key, probe.key)

    n2board = n2core.parent.parent
    r = cx_idxs[probe.slice]

    if probe.use_snip:
        probe.snip_info = dict(coreid=n2core.id, cxs=r, key=key)
    else:
        p = n2board.monitor.probe(n2core.cxState, r, key)
        core.board.map_probe(probe, p)


class LoihiSimulator(object):
    """Simulator to place a Model onto a Loihi board and run it.

    Parameters
    ----------
    cx_model : CxModel
        Model specification that will be placed on the Loihi board.
    seed : int, optional (Default: None)
        A seed for stochastic operations.

        .. warning :: Setting the seed has no effect on stochastic
                      operations run on the Loihi board.
    snip_max_spikes_per_step : int, optional (Default: 50)
        Maximum number of spikes that can be sent through
        the nengo_io_h2c channel on one timestep.
    """

    def __init__(self, cx_model, seed=None, snip_max_spikes_per_step=50):
        self.check_nxsdk_version()

        self.n2board = None
        self._probe_filters = {}
        self._probe_filter_pos = {}
        self.snip_max_spikes_per_step = snip_max_spikes_per_step

        nxsdk_dir = os.path.realpath(
            os.path.join(os.path.dirname(nxsdk.__file__), "..")
        )
        self.cwd = os.getcwd()
        logger.debug("cd to %s", nxsdk_dir)
        os.chdir(nxsdk_dir)

        if seed is not None:
            warnings.warn("Seed will be ignored when running on Loihi")

        # probeDict is a class attribute, so might contain things left over
        # from previous simulators
        N2SpikeProbe.probeDict.clear()

        self.build(cx_model, seed=seed)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @staticmethod
    def check_nxsdk_version():
        # raise exception if nxsdk not installed
        if callable(nxsdk):
            nxsdk()

        # if installed, check version
        version = LooseVersion(getattr(nxsdk, "__version__", "0.0.0"))
        minimum = LooseVersion("0.7.0")
        max_tested = LooseVersion("0.7.0")
        if version < minimum:
            raise ImportError("nengo-loihi requires nxsdk>=%s, found %s"
                              % (minimum, version))
        elif version > max_tested:
            warnings.warn("nengo-loihi has not been tested with your nxsdk "
                          "version (%s); latest fully supported version is "
                          "%s" % (version, max_tested))

    def build(self, cx_model, seed=None):
        cx_model.validate()
        self.model = cx_model

        # --- allocate --
        # maps CxModel to cores and chips
        allocator = one_to_one_allocator  # one core per ensemble
        self.board = allocator(self.model)

        # --- build
        self.n2board = build_board(self.board)

    def print_cores(self):
        for j, n2chip in enumerate(self.n2board.n2Chips):
            print("Chip %d, id=%d" % (j, n2chip.id))
            for k, n2core in enumerate(n2chip.n2Cores):
                print("  Core %d, id=%d" % (k, n2core.id))

    def run_steps(self, steps, blocking=True):
        # NOTE: we need to call connect() after snips are created
        self.connect()
        self.n2board.run(steps, aSync=not blocking)

    def wait_for_completion(self):
        self.n2board.finishRun()

    def is_connected(self):
        return self.n2board is not None and self.n2board.nxDriver.hasStarted()

    def connect(self, attempts=10):
        if self.n2board is None:
            raise SimulationError("Must build model before running")

        if self.is_connected():
            return

        logger.info("Connecting to Loihi, max attempts: %d", attempts)
        for i in range(attempts):
            try:
                self.n2board.startDriver()
                if self.is_connected():
                    break
            except Exception as e:
                logger.info("Connection error: %s", e)
                time.sleep(1)
                logger.info("Retrying, attempt %d", i + 1)
        else:
            raise SimulationError("Could not connect to the board")

    def close(self):
        self.n2board.disconnect()
        # TODO: can we chdir back earlier?
        if self.cwd is not None:
            logger.debug("cd to %s", self.cwd)
            os.chdir(self.cwd)
            self.cwd = None

        # clear inputs so we can safely start a new simulator if desired
        for input in self.model.cx_inputs:
            input.clear_loihi_input()

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

    def create_io_snip(self):
        # snips must be created before connecting
        assert not self.is_connected()

        snips_dir = os.path.join(os.path.dirname(__file__), "snips")
        env = jinja2.Environment(
            trim_blocks=True,
            loader=jinja2.FileSystemLoader(snips_dir),
            keep_trailing_newline=True
        )
        template = env.get_template("nengo_io.c.template")

        # --- generate custom code
        # Determine which cores have learning
        n_errors = 0
        total_error_len = 0
        max_error_len = 0
        for core in self.board.chips[0].cores:  # TODO: don't assume 1 chip
            if core.learning_coreid:
                error_len = core.groups[0].n // 2
                max_error_len = max(error_len, max_error_len)
                n_errors += 1
                total_error_len += 2 + error_len

        n_outputs = 1
        probes = []
        cores = set()
        # TODO: should snip_range be stored on the probe?
        snip_range = {}
        for group in self.model.cx_groups.keys():
            for probe in group.probes:
                if probe.use_snip:
                    info = probe.snip_info
                    assert info['key'] in ('u', 'v', 'spike')
                    # For spike probes, we record V and determine if the neuron
                    # spiked in Simulator.
                    cores.add(info["coreid"])
                    snip_range[probe] = slice(n_outputs - 1,
                                              n_outputs + len(info["cxs"]) - 1)
                    for cx in info["cxs"]:
                        probes.append(
                            (n_outputs, info["coreid"], cx, info['key']))
                        n_outputs += 1

        # --- write c file using template
        c_path = os.path.join(snips_dir, "nengo_io.c")
        logger.debug(
            "Creating %s with %d outputs, %d error, %d cores, %d probes",
            c_path, n_outputs, n_errors, len(cores), len(probes))
        code = template.render(
            n_outputs=n_outputs,
            n_errors=n_errors,
            max_error_len=max_error_len,
            cores=cores,
            probes=probes,
        )
        with open(c_path, 'w') as f:
            f.write(code)

        # --- create SNIP process and channels
        logger.debug("Creating nengo_io snip process")
        nengo_io = self.n2board.createProcess(
            name="nengo_io",
            cFilePath=c_path,
            includeDir=snips_dir,
            funcName="nengo_io",
            guardName="guard_io",
            phase="mgmt",
        )
        logger.debug("Creating nengo_learn snip process")
        self.n2board.createProcess(
            name="nengo_learn",
            cFilePath=os.path.join(snips_dir, "nengo_learn.c"),
            includeDir=snips_dir,
            funcName="nengo_learn",
            guardName="guard_learn",
            phase="preLearnMgmt",
        )

        size = self.snip_max_spikes_per_step * 2 + 1 + total_error_len
        logger.debug("Creating nengo_io_h2c channel")
        self.nengo_io_h2c = self.n2board.createChannel(b'nengo_io_h2c',
                                                       "int", size)
        logger.debug("Creating nengo_io_c2h channel")
        self.nengo_io_c2h = self.n2board.createChannel(b'nengo_io_c2h',
                                                       "int", n_outputs)
        self.nengo_io_h2c.connect(None, nengo_io)
        self.nengo_io_c2h.connect(nengo_io, None)
        self.nengo_io_c2h_count = n_outputs
        self.nengo_io_snip_range = snip_range

    def add_spikes_to_spikegen(self, spikes):
        # sort all spikes because spikegen needs them in temporal order
        spikes = sorted(spikes, key=lambda s: s.time)
        for spike in spikes:
            assert spike.axon.atom == 0, "Spikegen does not support atom"
            self.n2board.global_spike_generator.addSpike(
                time=spike.time, chipId=spike.axon.chip_id,
                coreId=spike.axon.core_id, axonId=spike.axon.axon_id)

    def send_spikes_errors(self, spikes, errors):
        max_spikes = self.snip_max_spikes_per_step
        if len(spikes) > max_spikes:
            warnings.warn("Too many spikes (%d) sent in one time "
                          "step.  Increase the value of "
                          "snip_max_spikes_per_step (currently "
                          "set to %d)" % (len(spikes), max_spikes))
            del spikes[max_spikes:]

        msg = [len(spikes)]
        for spike in spikes:
            assert spike.chip_id == 0
            msg.extend((spike.core_id, spike.axon_id))
        for error in errors:
            msg.extend(error)
        self.nengo_io_h2c.write(len(msg), msg)
