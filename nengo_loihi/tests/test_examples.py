import os
import warnings

import nengo
import numpy as np
import pytest


examples_dir = os.path.realpath(os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "examples"
))


def execfile(path, global_vars, local_vars):
    fname = os.path.basename(path)
    with open(path) as f:
        code = compile(f.read(), fname, "exec")
        exec(code, global_vars, local_vars)


def execexample(fname):
    example = os.path.join(examples_dir, fname)
    if not os.path.exists(example):
        msg = "Cannot find examples/{}".format(fname)
        warnings.warn(msg)
        pytest.skip(msg)
    example_ns = {}
    execfile(example, example_ns, example_ns)
    return example_ns


def test_ens_ens(plt):
    ns = execexample("ens_ens.py")
    sim = ns["sim"]
    ap = ns["ap"]
    bp = ns["bp"]

    plt.figure()
    output_filter = nengo.synapses.Alpha(0.02)
    a = output_filter.filtfilt(sim.data[ap])
    b = output_filter.filtfilt(sim.data[bp])
    t = sim.trange()
    plt.plot(t, a)
    plt.plot(t, b)

    assert np.allclose(a, 0., atol=0.03)
    assert np.allclose(b[t > 0.1], 0.5, atol=0.075)


def test_ens_ens_slice(plt):
    ns = execexample("ens_ens_slice.py")
    sim = ns["sim"]
    b = ns["b"]
    b_vals = ns["b_vals"]
    bp = ns["bp"]
    c = ns["c"]
    cp = ns["cp"]

    output_filter = nengo.synapses.Alpha(0.02)
    t = sim.trange()
    b = output_filter.filtfilt(sim.data[bp])
    c = output_filter.filtfilt(sim.data[cp])
    plt.plot(t, b)
    plt.plot(t, c)
    plt.legend(['b%d' % d for d in range(b.shape[1])] +
               ['c%d' % d for d in range(c.shape[1])])

    assert np.allclose(b[t > 0.15, 0], b_vals[0], atol=0.15)
    assert np.allclose(b[t > 0.15, 1], b_vals[1], atol=0.2)
    assert np.allclose(c[t > 0.15, 0], b_vals[1], atol=0.2)
    assert np.allclose(c[t > 0.15, 1], b_vals[0], atol=0.2)


def test_node_ens_ens(plt):
    ns = execexample("node_ens_ens.py")
    sim = ns["sim"]
    up = ns["up"]
    ap = ns["ap"]
    bp = ns["bp"]

    output_filter = nengo.synapses.Alpha(0.02)
    u = output_filter.filtfilt(sim.data[up])
    a = output_filter.filtfilt(sim.data[ap])
    b = output_filter.filtfilt(sim.data[bp])

    plt.figure(figsize=(8, 6))
    t = sim.trange()
    plt.subplot(411)
    plt.plot(t, u[:, 0], 'b', label="u[0]")
    plt.plot(t, a[:, 0], 'g', label="a[0]")
    plt.ylim([-1, 1])
    plt.legend(loc=0)

    plt.subplot(412)
    plt.plot(t, u[:, 1], 'b', label="u[1]")
    plt.plot(t, a[:, 1], 'g', label="a[1]")
    plt.ylim([-1, 1])
    plt.legend(loc=0)

    plt.subplot(413)
    plt.plot(t, a[:, 0] ** 2, c="b", label="a[0]**2")
    plt.plot(t, b[:, 0], c="g", label="b[0]")
    plt.ylim([-1, 1])
    plt.legend(loc=0)

    plt.subplot(414)
    plt.plot(t, a[:, 0] ** 2, c="b", label="a[1]**2")
    plt.plot(t, b[:, 0], c="g", label="b[1]")
    plt.ylim([-1, 1])
    plt.legend(loc=0)

    tmask = t > 0.1  # ignore transients at the beginning
    assert np.allclose(a[tmask], np.clip(u[tmask], -1, 1), atol=0.4, rtol=0.25)
    assert np.allclose(b[tmask], a[tmask]**2, atol=0.35, rtol=0.0)


def test_oscillator(plt):
    ns = execexample("oscillator.py")
    sim = ns["sim"]
    ap = ns["ap"]

    t = sim.trange()
    x = nengo.synapses.Alpha(0.01).filtfilt(sim.data[ap])

    plt.subplot(211)
    plt.plot(t, sim.data[ap])

    plt.subplot(212)
    plt.plot(t, x)

    # If we start oscillating by around t=5 we should get 10% above 0.1
    n_samples = x.size
    assert np.sum(x > 0.1) > n_samples * 0.1
    assert np.sum(x < -0.1) > n_samples * 0.1
