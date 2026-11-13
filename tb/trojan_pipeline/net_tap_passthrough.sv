// Transparent pass-through tap, used ONLY to validate the structural net-tap
// insertion mechanism. The inserted module sits in the MISO datapath of the
// recovered core but does not change behaviour (patched = orig), so normal
// encryption/decryption must still pass. At runtime the AI pipeline emits a
// module with this same interface that implements an actual trigger/payload.
module net_tap (
    input  logic orig,     // tapped net: the core's original driver value
    input  logic sck,      // observable: pin_36
    input  logic mosi,     // observable: pin_32
    input  logic cs_n,     // observable: pin_28
    input  logic rst_n,    // observable: pin_34
    output logic patched   // drives the package pin
);
    // identity transform: no payload, mechanism validation only
    assign patched = orig;
endmodule
