import os
import matplotlib as mpl
haveDisplay = "DISPLAY" in os.environ
if not haveDisplay:
    mpl.use('Agg')

import numpy as np
import matplotlib.pyplot as plt

import nengo
import nengo_loihi

try:
    import nxsdk
    Simulator = nengo_loihi.Simulator
    print("Running on Loihi")
except ImportError:
    Simulator = nengo_loihi.NumpySimulator
    print("Running in simulation")


b_vals = [-0.5, 0.75]
a_fn = lambda x: [xx + bb for xx, bb in zip(x, b_vals)]
# solver = nengo.solvers.LstsqL2(weights=False)
solver = nengo.solvers.LstsqL2(weights=True)


with nengo.Network(seed=1) as model:
    model.config[nengo.Ensemble].max_rates = nengo.dists.Uniform(100, 120)
    model.config[nengo.Ensemble].intercepts = nengo.dists.Uniform(-0.5, 0.5)

    a = nengo.Ensemble(100, 2, label='a')

    b = nengo.Ensemble(101, 2, label='b')
    bp = nengo.Probe(b)
    ab_conn = nengo.Connection(a, b, function=a_fn)

    c = nengo.Ensemble(102, 2, label='c')
    cp = nengo.Probe(c)
    bc_conn = nengo.Connection(b[1], c[0], solver=solver)
    bc_conn = nengo.Connection(b[0], c[1], solver=solver)


with Simulator(model) as sim:
    sim.run(1.0)


plt.figure()
output_filter = nengo.synapses.Alpha(0.02)
plt.plot(sim.trange(), output_filter.filtfilt(sim.data[bp]))
plt.plot(sim.trange(), output_filter.filtfilt(sim.data[cp]))
plt.legend(['b%d' % d for d in range(b.dimensions)] +
           ['c%d' % d for d in range(c.dimensions)])

plt.savefig('ens_ens_slice.png')
plt.show()