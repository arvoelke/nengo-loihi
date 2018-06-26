import pytest
import nengo
import numpy as np


def test_spike_units(Simulator, seed):
    with nengo.Network(seed=seed) as model:
        a = nengo.Ensemble(100, 1)
        p = nengo.Probe(a.neurons)
    with Simulator(model) as sim:
        sim.run(0.1)

    values = np.unique(sim.data[p])
    assert values[0] == 0
    assert values[1] == int(1.0 / sim.dt)
    assert len(values) == 2


@pytest.mark.parametrize('dim', [1, 2, 3])
def test_voltage_decode(Simulator, seed, plt, dim):
    with nengo.Network(seed=seed) as model:
        stim = nengo.Node(lambda t: [np.sin(2*np.pi*t)/np.sqrt(dim)]*dim)
        p_stim = nengo.Probe(stim, synapse=0.01)

        a = nengo.Ensemble(100*3, dim,
                           max_rates=nengo.dists.Uniform(100, 120),
                           intercepts=nengo.dists.Uniform(-.95, .95))
        nengo.Connection(stim, a, synapse=None)

        p_a = nengo.Probe(a, synapse=0.01)

    with Simulator(model, precompute=True) as sim:
        sim.run(1.)

    assert np.allclose(sim.data[p_stim], sim.data[p_a], atol=0.3)
