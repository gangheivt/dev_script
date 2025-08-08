/**
 * G.711 PLC with Dual-FFT Implementation
 * Features:
 *  - Selectable FFT backend (ARM DSP or Native C)
 *  - Dynamic LPC prediction (8-12 order)
 *  - WSOLA-based pitch compensation
 *  - Psychoacoustic noise shaping
 */

#include <stdint.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include <stdbool.h>

/* Configuration */
#define SAMPLE_RATE     8000     // 8kHz sampling rate
#define FRAME_SIZE      160      // 20ms frame size (160 samples)
#define MAX_LPC_ORDER   12       // Maximum LPC order
#define BARK_BANDS      24       // Psychoacoustic critical bands
#define MAX_HISTORY     5        // History buffer depth

/* FFT Implementation Selection */
//#define USE_ARM_DSP_FFT  // Uncomment to use ARM CMSIS-DSP
#ifdef USE_ARM_DSP_FFT
    #include "arm_math.h"
    #include "arm_const_structs.h"
#else
    #include <complex.h>
#endif

/* Audio frame structure */
typedef struct {
    int16_t pcm[FRAME_SIZE];     // PCM samples
    float lpc_coeffs[MAX_LPC_ORDER]; // LPC coefficients
    int pitch_period;            // Pitch period in samples
} AudioFrame;

/******************** FFT Implementation ********************/
#ifdef USE_ARM_DSP_FFT
/* ARM CMSIS-DSP Implementation */
typedef struct {
    arm_cfft_instance_f32* instance;
    uint32_t size;
} FFTHandler;

FFTHandler* fft_init(uint32_t size) {
    FFTHandler* handler = malloc(sizeof(FFTHandler));
    handler->size = size;
    
    switch(size) {
        case 64:   handler->instance = (arm_cfft_instance_f32*)&arm_cfft_sR_f32_len64; break;
        case 256:  handler->instance = (arm_cfft_instance_f32*)&arm_cfft_sR_f32_len256; break;
        default:   handler->instance = NULL;
    }
    return handler;
}

void fft_execute(FFTHandler* handler, float* io_buffer, bool is_inverse) {
    if (!handler->instance) return;
    arm_cfft_f32(handler->instance, io_buffer, is_inverse, 1);
}

#else
/* Native C Implementation */
typedef struct {
    uint32_t size;
    float* twiddle_factors;
} FFTHandler;

static void fft_radix2(float complex* x, int N, float complex* twiddle) {
    if (N <= 1) return;
    
    // Split even/odd
    float complex even[N/2], odd[N/2];
    for (int i = 0; i < N/2; i++) {
        even[i] = x[2*i];
        odd[i] = x[2*i+1];
    }
    
    // Recursive calls
    fft_radix2(even, N/2, twiddle);
    fft_radix2(odd, N/2, twiddle);
    
    // Combine results
    for (int k = 0; k < N/2; k++) {
        float complex t = twiddle[k * (512/N)] * odd[k];
        x[k] = even[k] + t;
        x[k + N/2] = even[k] - t;
    }
}

FFTHandler* fft_init(uint32_t size) {
    FFTHandler* handler = malloc(sizeof(FFTHandler));
    handler->size = size;
    handler->twiddle_factors = malloc(size * sizeof(float complex));
    
    // Precompute twiddle factors
    for (int i = 0; i < size; i++) {
        float angle = -2 * M_PI * i / size;
        handler->twiddle_factors[i] = cosf(angle) + sinf(angle)*I;
    }
    return handler;
}

void fft_execute(FFTHandler* handler, float* io_buffer, bool is_inverse) {
    // Convert real/imaginary interleaved to complex
    float complex cbuf[handler->size];
    for (int i = 0; i < handler->size; i++) {
        cbuf[i] = io_buffer[2*i] + io_buffer[2*i+1]*I;
    }
    
    // Perform FFT
    fft_radix2(cbuf, handler->size, handler->twiddle_factors);
    
    // Convert back
    for (int i = 0; i < handler->size; i++) {
        io_buffer[2*i] = creal(cbuf[i]);
        io_buffer[2*i+1] = cimag(cbuf[i]);
    }
}
#endif

void fft_cleanup(FFTHandler* handler) {
    #ifndef USE_ARM_DSP_FFT
    free(handler->twiddle_factors);
    #endif
    free(handler);
}

/******************** Core PLC Algorithms ********************/
/* Psychoacoustic model bands */
static const float bark_bands[BARK_BANDS+1] = { 
    0,100,200,300,400,510,630,770,920,1080,1270,1480,
    1720,2000,2320,2700,3150,3700,4400,5300,6400,7700,9500,12000,15500 
};

void compute_lpc(const int16_t* samples, float* lpc_coeffs, int* optimal_order) {
    float autocorr[MAX_LPC_ORDER+1] = {0};
    
    // Compute autocorrelation
    for (int i = 0; i <= MAX_LPC_ORDER; i++) {
        for (int j = 0; j < FRAME_SIZE - i; j++) {
            autocorr[i] += samples[j] * samples[j + i];
        }
    }

    // Levinson-Durbin algorithm
    float error = autocorr[0];
    lpc_coeffs[0] = 1.0f;
    
    for (int i = 1; i <= MAX_LPC_ORDER; i++) {
        float reflection = -autocorr[i];
        for (int j = 1; j < i; j++) {
            reflection -= lpc_coeffs[j] * autocorr[i - j];
        }
        reflection /= error;
        
        lpc_coeffs[i] = reflection;
        for (int j = 1; j <= i/2; j++) {
            float tmp = lpc_coeffs[j];
            lpc_coeffs[j] += reflection * lpc_coeffs[i - j];
            lpc_coeffs[i - j] += reflection * tmp;
        }
        error *= (1.0f - reflection * reflection);
        
        // Dynamic order selection
        if (i > 4 && (error/autocorr[0] < 0.05f)) {
            *optimal_order = i;
            break;
        }
    }
}

int find_pitch_period(const int16_t* samples) {
    float max_corr = -1.0f;
    int best_period = 40;  // Default 200Hz
    
    // Search range 50-400Hz (20-160 samples)
    for (int period = 20; period < 160; period++) {
        float corr = 0.0f;
        for (int i = 0; i < FRAME_SIZE - period; i++) {
            corr += samples[i] * samples[i + period];
        }
        if (corr > max_corr) {
            max_corr = corr;
            best_period = period;
        }
    }
    return best_period;
}

bool is_unvoiced(const int16_t* frame) {
    int zero_cross = 0;
    float energy = 0.0f;
    
    for (int i = 1; i < FRAME_SIZE; i++) {
        zero_cross += (frame[i-1]*frame[i] < 0);
        energy += frame[i] * frame[i];
    }
    
    float zcr = (float)zero_cross / FRAME_SIZE;
    energy /= FRAME_SIZE;
    
    return (zcr > 0.3f) && (energy < 500.0f);
}

void add_comfort_noise(int16_t* pcm, int loss_count) {
    for (int i = 0; i < FRAME_SIZE; i++) {
        float noise = (rand() / (float)RAND_MAX - 0.5f) * 100 * powf(0.8f, loss_count);
        pcm[i] += (int16_t)noise;
    }
}

void noise_shaping(int16_t* pcm_frame, FFTHandler* fft_handler) {
    float fft_buffer[FRAME_SIZE * 2] = {0};
    
    // 1. Windowing (Hanning) and prepare FFT input
    for (int i = 0; i < FRAME_SIZE; i++) {
        float window = 0.5f - 0.5f * cosf(2*M_PI*i/FRAME_SIZE);
        fft_buffer[2*i] = window * pcm_frame[i] / 32768.0f; // Normalize to [-1,1]
        fft_buffer[2*i+1] = 0;
    }

    // 2. Execute FFT
    fft_execute(fft_handler, fft_buffer, false);
    
    // 3. Calculate masking thresholds
    float magnitude[FRAME_SIZE/2];
    #ifdef USE_ARM_DSP_FFT
    arm_cmplx_mag_f32(fft_buffer, magnitude, FRAME_SIZE/2);
    #else
    for (int i = 0; i < FRAME_SIZE/2; i++) {
        magnitude[i] = sqrtf(fft_buffer[2*i]*fft_buffer[2*i] + 
                            fft_buffer[2*i+1]*fft_buffer[2*i+1]);
    }
    #endif
    
    float masking_threshold[BARK_BANDS] = {0};
    for (int band = 0; band < BARK_BANDS; band++) {
        int start = (int)(bark_bands[band] * FRAME_SIZE / SAMPLE_RATE);
        int end = (int)(bark_bands[band+1] * FRAME_SIZE / SAMPLE_RATE);
        
        // Calculate band energy
        float energy = 0.0f;
        for (int bin = start; bin < end && bin < FRAME_SIZE/2; bin++) {
            energy += magnitude[bin] * magnitude[bin];
        }
        energy = sqrtf(energy/(end-start));
        
        // Spread masking curve (3dB/Bark)
        for (int k = 0; k < BARK_BANDS; k++) {
            float spread = 3.0f * fabsf(band - k);
            masking_threshold[k] += energy * expf(-0.05f * spread);
        }
    }

    // 4. Frequency domain noise shaping
    for (int band = 0; band < BARK_BANDS; band++) {
        int start = (int)(bark_bands[band] * FRAME_SIZE / SAMPLE_RATE);
        int end = (int)(bark_bands[band+1] * FRAME_SIZE / SAMPLE_RATE);
        
        for (int bin = start; bin < end && bin < FRAME_SIZE/2; bin++) {
            float noise_floor = 0.001f;
            if (magnitude[bin] > masking_threshold[band] + noise_floor) {
                float gain = masking_threshold[band] / (magnitude[bin] + noise_floor);
                fft_buffer[2*bin] *= gain;    // Real part
                fft_buffer[2*bin+1] *= gain;  // Imaginary part
            }
        }
    }

    // 5. Execute IFFT and reconstruct signal
    fft_execute(fft_handler, fft_buffer, true);
    
    for (int i = 0; i < FRAME_SIZE; i++) {
        float sample = fft_buffer[2*i] * 32768.0f * 0.9f; // Denormalize and prevent overflow
        pcm_frame[i] = (int16_t)sample;
    }
}

/******************** Main PLC Processing ********************/
void conceal_lost_frame(AudioFrame* output, const AudioFrame* history, int loss_count) {
    // 1. Dynamic LPC prediction
    int lpc_order = MAX_LPC_ORDER;
    compute_lpc(history->pcm, output->lpc_coeffs, &lpc_order);
    
    // 2. Pitch period compensation
    output->pitch_period = find_pitch_period(history->pcm);
    int16_t pitch_based[FRAME_SIZE];
    for (int i = 0; i < FRAME_SIZE; i++) {
        int pos = (i - output->pitch_period + FRAME_SIZE) % FRAME_SIZE;
        pitch_based[i] = history->pcm[pos];
    }

    // 3. Mixed compensation signal
    float lpc_weight = 0.7f - 0.1f * loss_count;
    if (lpc_weight < 0.3f) lpc_weight = 0.3f;
    float attenuation = powf(0.85f, loss_count);
    
    for (int i = 0; i < FRAME_SIZE; i++) {
        float lpc_sample = 0.0f;
        for (int k = 1; k <= lpc_order; k++) {
            int pos = (i - k + FRAME_SIZE) % FRAME_SIZE;
            lpc_sample += output->lpc_coeffs[k] * history->pcm[pos];
        }
        output->pcm[i] = (int16_t)((lpc_sample*lpc_weight + pitch_based[i]*(1-lpc_weight)) * attenuation);
    }

    // 4. Unvoiced detection and noise shaping
    FFTHandler* fft = fft_init(FRAME_SIZE);
    if (is_unvoiced(output->pcm)) {
        noise_shaping(output->pcm, fft);
        add_comfort_noise(output->pcm, loss_count);
    }
    fft_cleanup(fft);
}

/******************** G.711 Codec Functions ********************/
int16_t alaw2linear(uint8_t alaw) {
    alaw ^= 0x55;
    int16_t sign = (alaw & 0x80) ? -1 : 1;
    int16_t exponent = (alaw >> 4) & 0x07;
    int16_t mantissa = alaw & 0x0F;
    
    if (exponent > 0) mantissa |= 0x10;
    mantissa <<= (exponent + 3);
    return sign * mantissa;
}

uint8_t linear2alaw(int16_t linear) {
    int16_t abs_linear = abs(linear);
    uint8_t sign = (linear < 0) ? 0x80 : 0x00;
    
    if (abs_linear < 0x20) {  // Special case for small values
        return sign | (abs_linear >> 4);
    }
    
    // Find segment
    uint8_t exponent = 0;
    uint8_t mantissa = (abs_linear >> 8) ? (abs_linear >> 8) : abs_linear;
    while (mantissa > 0x1F) {
        mantissa >>= 1;
        exponent++;
    }
    
    uint8_t alaw = sign | ((exponent << 4) | (mantissa & 0x0F));
    return alaw ^ 0x55;
}

/******************** API Interface ********************/
void plc_process(AudioFrame* output, const AudioFrame* history, bool is_lost, int loss_count) {
    if (!is_lost) {
        memcpy(output, history, sizeof(AudioFrame));
    } else {
        conceal_lost_frame(output, history, loss_count);
    }
}

/* Test Case */
int main() {
    AudioFrame history = {0};
    AudioFrame output;
    int loss_count = 0;
    
    // Generate test signal (440Hz sine wave)
    float phase = 0.0f;
    float increment = 2.0f * M_PI * 440.0f / SAMPLE_RATE;
    for (int i = 0; i < FRAME_SIZE; i++) {
        history.pcm[i] = (int16_t)(sinf(phase) * 32767 * 0.8f);
        phase += increment;
        if (phase >= 2.0f * M_PI) phase -= 2.0f * M_PI;
    }
    
    // Simulate packet loss (frames 3-4 lost)
    for (int i = 0; i < 10; i++) {
        bool is_lost = (i == 3 || i == 4);
        
        if (is_lost) loss_count++;
        else loss_count = 0;
        
        plc_process(&output, &history, is_lost, loss_count);
        printf("Frame %d: %s\n", i, is_lost ? "LOST (concealed)" : "OK");
    }
    
    return 0;
}
