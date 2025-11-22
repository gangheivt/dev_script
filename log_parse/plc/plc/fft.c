#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <math.h>
#include "plc.h"
/* --------------------------------------------------------------------------
 * ARM CMSIS-DSP FFT Implementation (if enabled)
 * -------------------------------------------------------------------------- */
#ifdef USE_ARM_DSP_FFT

 // Include CMSIS-DSP headers (ensure path is correct for your project)
#include "arm_math.h"
#include "arm_const_structs.h"

/* Initialize ARM CMSIS-DSP FFT */
FFTHandler* fft_init(uint32_t size) {
    FFTHandler* handler = (FFTHandler*)malloc(sizeof(FFTHandler));
    if (!handler) return NULL;

    handler->size = size;
    handler->instance = NULL;

    // Map FFT size to pre-defined CMSIS instances (supports common sizes)
    switch (size) {
    case 16:   handler->instance = &arm_cfft_sR_f32_len16;   break;
    case 32:   handler->instance = &arm_cfft_sR_f32_len32;   break;
    case 64:   handler->instance = &arm_cfft_sR_f32_len64;   break;
    case 128:  handler->instance = &arm_cfft_sR_f32_len128;  break;
    case 256:  handler->instance = &arm_cfft_sR_f32_len256;  break;
    case 512:  handler->instance = &arm_cfft_sR_f32_len512;  break;
    case 1024: handler->instance = &arm_cfft_sR_f32_len1024; break;
    default:
        free(handler);
        return NULL; // Unsupported size
    }

    return handler;
}

/* Execute ARM CMSIS-DSP FFT (forward/inverse) */
void fft_execute(FFTHandler* handler, float* io_buffer, bool is_inverse) {
    if (!handler || !handler->instance || !io_buffer) return;

    // CMSIS FFT expects 32-bit floating point interleaved (real, imag)
    // For inverse FFT: set "ifftFlag" to 1, "bitReverseFlag" to 1
    arm_cfft_f32(handler->instance, io_buffer, is_inverse ? 1 : 0, 1);

    // Scale inverse FFT results (CMSIS doesn't auto-scale)
    if (is_inverse) {
        float scale = 1.0f / handler->size;
        arm_scale_f32(io_buffer, scale, io_buffer, 2 * handler->size);
    }
}

/* Cleanup ARM CMSIS-DSP FFT */
void fft_cleanup(FFTHandler* handler) {
    if (handler) free(handler);
}

/* --------------------------------------------------------------------------
 * Native Radix-2 FFT Implementation (fallback)
 * -------------------------------------------------------------------------- */
#else

 /* Helper: Bit-reversal permutation for FFT input */
static void bit_reverse(float* real, float* imag, uint32_t n) {
    uint32_t i, j, k;
    for (i = 1, j = n / 2; i < n - 1; i++) {
        if (i < j) {
            // Swap real parts
            float temp = real[i];
            real[i] = real[j];
            real[j] = temp;
            // Swap imaginary parts
            temp = imag[i];
            imag[i] = imag[j];
            imag[j] = temp;
        }
        k = n / 2;
        while (j >= k) {
            j -= k;
            k /= 2;
        }
        j += k;
    }
}

/* Initialize native FFT with twiddle factors */
FFTHandler* fft_init(int size) {
    // Check if size is a power of 2 (required for radix-2)
    if ((size & (size - 1)) != 0) return NULL;

    FFTHandler* handler = (FFTHandler*)malloc(sizeof(FFTHandler));
    if (!handler) return NULL;

    handler->size = size;
    handler->twiddle_real = (float*)malloc(size * sizeof(float));
    handler->twiddle_imag = (float*)malloc(size * sizeof(float));
    if (!handler->twiddle_real || !handler->twiddle_imag) {
        free(handler->twiddle_real);
        free(handler->twiddle_imag);
        free(handler);
        return NULL;
    }

    // Precompute twiddle factors: e^(-2¦Ðik/N) = cos¦È - i sin¦È
    for (uint32_t k = 0; k < size; k++) {
        float angle = -2.0f * (float)M_PI * k / size;
        handler->twiddle_real[k] = cosf(angle);
        handler->twiddle_imag[k] = sinf(angle);
    }

    return handler;
}

/* Execute native FFT (forward/inverse) */
void fft_execute(FFTHandler* handler, float* io_buffer, bool is_inverse) {
    if (!handler || !io_buffer) return;

    uint32_t n = handler->size;
    float* real = (float*)_alloca(n * sizeof(float));  // Stack allocation (MSVC)
    float* imag = (float*)_alloca(n * sizeof(float));

    // Extract real/imaginary parts from interleaved buffer
    for (uint32_t i = 0; i < n; i++) {
        real[i] = io_buffer[2 * i];       // Even indices = real
        imag[i] = io_buffer[2 * i + 1];   // Odd indices = imaginary
    }

    // Bit-reverse input (required for radix-2)
    bit_reverse(real, imag, n);

    // Radix-2 FFT butterfly operations
    for (uint32_t m = 2; m <= n; m *= 2) {
        uint32_t mh = m / 2;
        for (uint32_t i = 0; i < n; i += m) {
            for (uint32_t j = 0; j < mh; j++) {
                uint32_t idx = i + j;
                uint32_t k = idx + mh;
                uint32_t tw_idx = (handler->size / m) * j;  // Twiddle index

                // Get twiddle factor (conjugate for inverse FFT)
                float wr = handler->twiddle_real[tw_idx];
                float wi = is_inverse ? handler->twiddle_imag[tw_idx] : -handler->twiddle_imag[tw_idx];

                // Butterfly calculation
                float tr = wr * real[k] - wi * imag[k];
                float ti = wr * imag[k] + wi * real[k];
                real[k] = real[idx] - tr;
                imag[k] = imag[idx] - ti;
                real[idx] += tr;
                imag[idx] += ti;
            }
        }
    }

    // Scale inverse FFT results
    if (is_inverse) {
        float scale = 1.0f / n;
        for (uint32_t i = 0; i < n; i++) {
            real[i] *= scale;
            imag[i] *= scale;
        }
    }

    // Pack results back into interleaved buffer
    for (uint32_t i = 0; i < n; i++) {
        io_buffer[2 * i] = real[i];
        io_buffer[2 * i + 1] = imag[i];
    }
}

/* Cleanup native FFT */
void fft_cleanup(FFTHandler* handler) {
    if (handler) {
        free(handler->twiddle_real);
        free(handler->twiddle_imag);
        free(handler);
    }
}

#endif /* USE_ARM_DSP_FFT */

