import numpy as np

import nengo
import nengo.utils.matplotlib
import pytest

import nengo_loihi


def test_precompute(Simulator, seed, plt):
    with nengo.Network(seed=seed) as model:
        D = 2
        stim = nengo.Node(lambda t: [np.sin(t * 2 * np.pi)] * D)

        a = nengo.Ensemble(100, D,
                           max_rates=nengo.dists.Uniform(100, 120),
                           intercepts=nengo.dists.Uniform(-0.5, 0.5))

        nengo.Connection(stim, a)

        output = nengo.Node(None, size_in=D)

        nengo.Connection(a, output)

        p_stim = nengo.Probe(stim, synapse=0.03)
        p_a = nengo.Probe(a, synapse=0.03)
        p_out = nengo.Probe(output, synapse=0.03)

    with Simulator(model, precompute=False) as sim1:
        sim1.run(1.0)
    with Simulator(model, precompute=True) as sim2:
        sim2.run(1.0)

    plt.subplot(2, 1, 1)
    plt.plot(sim1.trange(), sim1.data[p_stim])
    plt.plot(sim1.trange(), sim1.data[p_a])
    plt.plot(sim1.trange(), sim1.data[p_out])
    plt.title('precompute=False')
    plt.subplot(2, 1, 2)
    plt.plot(sim2.trange(), sim2.data[p_stim])
    plt.plot(sim2.trange(), sim2.data[p_a])
    plt.plot(sim2.trange(), sim2.data[p_out])
    plt.title('precompute=True')

    assert np.array_equal(sim1.data[p_stim], sim2.data[p_stim])
    assert np.array_equal(sim1.data[p_a], sim2.data[p_a])
    assert np.array_equal(sim1.data[p_out], sim2.data[p_out])


@pytest.mark.parametrize('D', [1, 3])
def test_precompute_max_time(Simulator, seed, plt, D):
    with nengo.Network(seed=seed) as model:
        stim = nengo.Node(lambda t: [np.sin(t * 2 * np.pi)] * D)

        a = nengo.Ensemble(100 * D, D,
                           max_rates=nengo.dists.Uniform(100, 120),
                           intercepts=nengo.dists.Uniform(-0.5, 0.5))
        nengo.Connection(stim, a)

        p_stim = nengo.Probe(stim, synapse=0.1)
        p_a = nengo.Probe(a, synapse=0.1)
    with Simulator(model, max_time=1.0) as sim:
        sim.run(1.0)

    plt.subplot(2, 1, 1)
    plt.plot(sim.trange(), sim.data[p_stim])
    plt.title('Input (should be %d sine waves)' % D)
    plt.legend(['%d' % i for i in range(D)], loc='best')
    plt.subplot(2, 1, 2)
    plt.title('Output (should be %d sine waves)' % D)
    plt.plot(sim.trange(), sim.data[p_a])
    plt.legend(['%d' % i for i in range(D)], loc='best')

    assert np.allclose(sim.data[p_stim], sim.data[p_a], atol=0.3)


def test_input_node_precompute(plt):
    pytest.importorskip('nxsdk')

    input_fn = lambda t: np.sin(2*np.pi*t)
    sims = {'simulation': nengo_loihi.NumpySimulator,
            'silicon': nengo_loihi.Simulator}
    x = {}
    u = {}
    v = {}
    for name, simulator in sims.items():
        n = 4
        with nengo.Network(seed=1) as model:
            inp = nengo.Node(input_fn)

            a = nengo.Ensemble(n, 1,
                               max_rates=nengo.dists.Uniform(100, 120),
                               intercepts=nengo.dists.Uniform(-0.5, 0.5))
            ap = nengo.Probe(a, synapse=0.01)
            aup = nengo.Probe(a.neurons, 'input')
            avp = nengo.Probe(a.neurons, 'voltage')

            nengo.Connection(inp, a)

        with simulator(model, precompute=True) as sim:
            print("Running in {}".format(name))
            sim.run(3.)

        synapse = nengo.synapses.Lowpass(0.03)
        x[name] = synapse.filt(sim.data[ap])

        u[name] = sim.data[aup][:25]
        u[name] = (np.round(u[name]*1000)
                   if str(u[name].dtype).startswith('float') else u[name])

        v[name] = sim.data[avp][:25]
        v[name] = (np.round(v[name]*1000)
                   if str(v[name].dtype).startswith('float') else v[name])

        plt.plot(sim.trange(), x[name], label=name)

    t = sim.trange()
    u = input_fn(t)
    plt.plot(t, u, 'k:', label='input')
    plt.legend(loc='best')

    assert np.allclose(x['silicon'], x['simulation'], atol=0.1, rtol=0.01)
