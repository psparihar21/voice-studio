// Copies each block of microphone samples to the page, which keeps them as 16-bit PCM.
class Recorder extends AudioWorkletProcessor {
  process(inputs) {
    const c = inputs[0] && inputs[0][0];
    if (c) this.port.postMessage(c.slice(0));
    return true;
  }
}
registerProcessor('rec', Recorder);
