#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <mmsystem.h>
#include <math.h>
#include "plc.h"
#include "audio_msbc_plc.h"
#include <time.h>

// 配置参数（可根据需要调整）
#define BUFFER_COUNT 4         // 多缓冲区数量（推荐4个）
#define MAX_QUEUED_BUFFERS 4   // 最大预队列缓冲区数量

typedef struct {   
    float real;
    float imag;
} Complex;

/* WAV Header Structure */
typedef struct {
    char riff[4];         // "RIFF"
    uint32_t file_size;   // Total file size - 8
    char wave[4];         // "WAVE"
    char fmt[4];          // "fmt "
    uint32_t fmt_size;    // 16 for PCM
    uint16_t audio_format;// 1 = PCM
    uint16_t num_channels;// 1 = mono, 2 = stereo
    uint32_t sample_rate; // Original sample rate
    uint32_t byte_rate;   // sample_rate * channels * (bits/8)
    uint16_t block_align; // channels * (bits/8)
    uint16_t bits_per_sample; // 16
    char data[4];         // "data"
    uint32_t data_size;   // PCM data size
} WavHeader;

/* 音频缓冲区结构 */
typedef struct {
    int16_t pcm[FRAME_SIZE];  // 音频数据
    WAVEHDR header;           // Windows音频头
    bool in_use;              // 是否正在被使用
} AudioBuffer;

/* 全局变量 */
static AudioBuffer g_buffers[BUFFER_COUNT];  // 多缓冲区
static HWAVEOUT hWaveOut;
static FILE* wav_file = NULL;
static uint32_t wav_sample_rate;
static uint16_t wav_channels;
static volatile int g_queued_count = 0;      // 已队列化的缓冲区数量
static volatile bool g_playback_active = false;  // 播放状态

/* 线性重采样（保持原逻辑） */
int linear_resample(const int16_t* input, int input_samples,
    int input_rate, int output_rate,
    int16_t* output, int max_output) {

    if (input_rate == output_rate) {
        int copy = min(input_samples, max_output);
        memcpy(output, input, copy * sizeof(int16_t));
        return copy;
    }

    int16_t* filtered_input = NULL;
    int filtered_samples = input_samples;

    if (input_rate > output_rate) {
        filtered_input = malloc(input_samples * sizeof(int16_t));
        if (!filtered_input) return 0;

        const int filter_taps = 7;
        for (int i = 0; i < input_samples; i++) {
            int32_t sum = 0;
            int valid_taps = 0;
            for (int t = -filter_taps / 2; t <= filter_taps / 2; t++) {
                int pos = i + t;
                if (pos >= 0 && pos < input_samples) {
                    sum += input[pos];
                    valid_taps++;
                }
            }
            filtered_input[i] = (int16_t)(sum / (valid_taps > 0 ? valid_taps : 1));
        }
    }
    else {
        filtered_input = (int16_t*)input;
    }

    float ratio = (float)input_rate / output_rate;
    int output_samples = min((int)ceil(input_samples / ratio), max_output);

    for (int i = 0; i < output_samples; i++) {
        float input_pos = i * ratio;
        int idx = (int)floorf(input_pos);
        float frac = input_pos - idx;

        if (idx < 0) idx = 0;
        if (idx >= input_samples) idx = input_samples - 1;

        int16_t sample;
        if (idx + 1 < input_samples) {
            sample = (int16_t)(filtered_input[idx] * (1.0f - frac) + filtered_input[idx + 1] * frac);
        }
        else {
            sample = filtered_input[idx];
        }

        if (sample > 32767) sample = 32767;
        if (sample < -32768) sample = -32768;
        output[i] = sample;
    }

    if (input_rate > output_rate&& filtered_input != input) {
        free(filtered_input);
    }

    return output_samples;
}

/* WAV文件初始化（保持原逻辑） */
bool init_wav_input(const char* wav_path) {
    wav_file = NULL;
    fopen_s(&wav_file, wav_path, "rb");
    if (!wav_file) {
        printf("Failed to open WAV file: %s\n", wav_path);
        return false;
    }

    WavHeader header;
    if (fread(&header, sizeof(WavHeader), 1, wav_file) != 1) {
        printf("Invalid WAV header\n");
        fclose(wav_file);
        return false;
    }

    if (memcmp(header.riff, "RIFF", 4) != 0 ||
        memcmp(header.wave, "WAVE", 4) != 0 ||
        memcmp(header.fmt, "fmt ", 4) != 0 ||
        memcmp(header.data, "data", 4) != 0) {
        printf("Not a valid WAV file\n");
        fclose(wav_file);
        return false;
    }

    if (header.audio_format != 1 || header.bits_per_sample != 16) {
        printf("WAV must be 16-bit PCM\n");
        fclose(wav_file);
        return false;
    }

    wav_sample_rate = header.sample_rate;
    wav_channels = header.num_channels;
    printf("WAV loaded: %u Hz, %u channels\n", wav_sample_rate, wav_channels);
    return true;
}

/* 读取WAV并转换为8kHz mono（优化：预分配内存） */
int read_wav_8khz(int16_t* output, int max_samples, int16_t* raw_pcm_buf, int16_t* mono_pcm_buf, int max_raw_samples) {
    int samples_needed = (int)(max_samples * (wav_sample_rate / (float)SAMPLE_RATE));
    if (samples_needed > max_raw_samples) {
        samples_needed = max_raw_samples;  // 防止缓冲区溢出
    }

    static cnt = 0;
    printf("%d", cnt++);
    // 读取原始PCM数据（复用预分配缓冲区）
    size_t bytes_read = fread(raw_pcm_buf, sizeof(int16_t), samples_needed * wav_channels, wav_file);
    if (bytes_read < samples_needed * wav_channels) {
        return 0;
    }
    int frames_read = bytes_read / wav_channels;

    // 转换为单声道（复用预分配缓冲区）
    for (int i = 0; i < frames_read; i++) {
        if (wav_channels == 1) {
            mono_pcm_buf[i] = raw_pcm_buf[i];
        }
        else {
            mono_pcm_buf[i] = (raw_pcm_buf[2 * i] + raw_pcm_buf[2 * i + 1]) / 2;
        }
    }

    // 重采样到8kHz
    return linear_resample(mono_pcm_buf, frames_read, wav_sample_rate, SAMPLE_RATE, output, max_samples);
}

/* 音频回调函数（缓冲区完成时触发） */
void CALLBACK waveOutProc(HWAVEOUT hwo, UINT uMsg, DWORD_PTR dwInstance, DWORD_PTR dwParam1, DWORD_PTR dwParam2) {
    if (uMsg != WOM_DONE) return;

    // 标记缓冲区为可用
    WAVEHDR* pHdr = (WAVEHDR*)dwParam1;
    AudioBuffer* pBuffer = NULL;

    // 找到对应的缓冲区
    for (int i = 0; i < BUFFER_COUNT; i++) {
        if (&g_buffers[i].header == pHdr) {
            pBuffer = &g_buffers[i];
            break;
        }
    }

    if (pBuffer) {
        waveOutUnprepareHeader(hWaveOut, &pBuffer->header, sizeof(WAVEHDR));
        pBuffer->in_use = false;
        g_queued_count--;  // 减少队列计数
    }
}

/* 初始化音频输出（多缓冲区+回调） */
bool init_audio_output() {
    WAVEFORMATEX waveFormat = { 0 };
    waveFormat.wFormatTag = WAVE_FORMAT_PCM;
    waveFormat.nChannels = 1;
    waveFormat.nSamplesPerSec = SAMPLE_RATE;
    waveFormat.wBitsPerSample = 16;
    waveFormat.nBlockAlign = (waveFormat.nChannels * waveFormat.wBitsPerSample) / 8;
    waveFormat.nAvgBytesPerSec = waveFormat.nSamplesPerSec * waveFormat.nBlockAlign;
    waveFormat.cbSize = 0;

    // 打开音频设备，使用回调模式
    MMRESULT result = waveOutOpen(&hWaveOut, WAVE_MAPPER, &waveFormat,
        (DWORD_PTR)waveOutProc, 0, CALLBACK_FUNCTION);
    if (result != MMSYSERR_NOERROR) {
        printf("Failed to open audio device. Error: %d\n", result);
        return false;
    }

    // 初始化所有缓冲区
    for (int i = 0; i < BUFFER_COUNT; i++) {
        memset(&g_buffers[i], 0, sizeof(AudioBuffer));
        g_buffers[i].header.lpData = (LPSTR)g_buffers[i].pcm;
        g_buffers[i].header.dwBufferLength = FRAME_SIZE * sizeof(int16_t);
        g_buffers[i].in_use = false;
    }

    g_playback_active = true;
    return true;
}

/* 填充并播放缓冲区（非阻塞） */
bool queue_buffer(const int16_t* pcm_data) {
    // 等待有可用缓冲区
    while (g_queued_count >= MAX_QUEUED_BUFFERS) {
        Sleep(1);  // 短暂等待，避免CPU占用过高
        if (!g_playback_active) return false;
    }

    // 找到空闲缓冲区
    AudioBuffer* pBuffer = NULL;
    for (int i = 0; i < BUFFER_COUNT; i++) {
        if (!g_buffers[i].in_use) {
            pBuffer = &g_buffers[i];
            break;
        }
    }

    if (!pBuffer) return false;

    // 填充数据并提交播放
    memcpy(pBuffer->pcm, pcm_data, FRAME_SIZE * sizeof(int16_t));
    pBuffer->header.dwFlags = 0;

    MMRESULT result = waveOutPrepareHeader(hWaveOut, &pBuffer->header, sizeof(WAVEHDR));
    if (result != MMSYSERR_NOERROR) {
        printf("Failed to prepare buffer. Error: %d\n", result);
        return false;
    }

    result = waveOutWrite(hWaveOut, &pBuffer->header, sizeof(WAVEHDR));
    if (result != MMSYSERR_NOERROR) {
        printf("Failed to write buffer. Error: %d\n", result);
        waveOutUnprepareHeader(hWaveOut, &pBuffer->header, sizeof(WAVEHDR));
        return false;
    }

    pBuffer->in_use = true;
    g_queued_count++;
    return true;
}

/* 清理资源 */
void cleanup_audio_output() {
    g_playback_active = false;

    // 等待所有缓冲区完成
    while (g_queued_count > 0) {
        Sleep(10);
    }

    // 清理设备
    if (hWaveOut) {
        waveOutClose(hWaveOut);
        hWaveOut = NULL;
    }
}

void cleanup_wav_input() {
    if (wav_file) fclose(wav_file);
}

#if 0
/* 主函数 */
int main() {
    AudioFrame history = { 0 };
    AudioFrame output;
    int loss_count = 0;
    int total_samples = 0;

    // 预分配WAV处理缓冲区（避免频繁malloc）
    const int MAX_RAW_SAMPLES = 4096;  // 根据需要调整大小
    int16_t* raw_pcm_buf = malloc(MAX_RAW_SAMPLES * sizeof(int16_t));
    int16_t* mono_pcm_buf = malloc(MAX_RAW_SAMPLES * sizeof(int16_t));
    if (!raw_pcm_buf || !mono_pcm_buf) {
        printf("Memory allocation failed\n");
        return 1;
    }

    srand((unsigned int)time(NULL));

    if (!init_wav_input("input.wav")) {
        free(raw_pcm_buf);
        free(mono_pcm_buf);
        return 1;
    }

    if (!init_audio_output()) {
        cleanup_wav_input();
        free(raw_pcm_buf);
        free(mono_pcm_buf);
        return 1;
    }

    printf("Playing WAV with small FRAME_SIZE (%d) ...\n", FRAME_SIZE);

    // 持续填充缓冲区
    while (g_playback_active) {
        int samples_read = read_wav_8khz(history.pcm, FRAME_SIZE, raw_pcm_buf, mono_pcm_buf, MAX_RAW_SAMPLES);
        if (samples_read < FRAME_SIZE) {
            break;  // 文件结束
        }

        bool is_lost = (rand() % 100) > 70;
        loss_count = is_lost ? loss_count + 1 : 0;
        plc_process(&output, &history, is_lost, loss_count);

        if (!queue_buffer(output.pcm)) {
            break;
        }

        total_samples += FRAME_SIZE;
        printf("Time: %.1fs | Frame: %s | Queued: %d\n",
            total_samples / (float)SAMPLE_RATE,
            is_lost ? "LOST (concealed)" : "OK",
            g_queued_count);
    }

    // 等待所有缓冲区播放完成
    cleanup_audio_output();
    cleanup_wav_input();
    free(raw_pcm_buf);
    free(mono_pcm_buf);

    printf("Playback finished. Total samples: %d\n", total_samples);
    return 0;
}

#else

// 新增：写入WAV文件的函数
void write_wav(const char* path, const int16_t* pcm, int total_samples, int sample_rate) {
    FILE* f = fopen(path, "wb");
    if (!f) return;

    WavHeader hdr = { 0 };
    memcpy(hdr.riff, "RIFF", 4);
    memcpy(hdr.wave, "WAVE", 4);
    memcpy(hdr.fmt, "fmt ", 4);
    memcpy(hdr.data, "data", 4);
    hdr.fmt_size = 16;
    hdr.audio_format = 1; // PCM
    hdr.num_channels = 1; // 单声道
    hdr.sample_rate = sample_rate;
    hdr.bits_per_sample = 16;
    hdr.block_align = hdr.num_channels * hdr.bits_per_sample / 8;
    hdr.byte_rate = hdr.sample_rate * hdr.block_align;
    hdr.data_size = total_samples * hdr.block_align;
    hdr.file_size = 36 + hdr.data_size; // 36 = 4+4+4+4+4+2+2+4+4+2+2 + 4（data字段）

    fwrite(&hdr, sizeof(hdr), 1, f);
    fwrite(pcm, sizeof(int16_t), total_samples, f);
    fclose(f);
}

#define TOTAL_FRAMES 10000 // 根据实际音频长度调整
int16_t ref_pcm[TOTAL_FRAMES * FRAME_SIZE] = { 0 };    // 参考音频
int16_t with_plc_pcm[TOTAL_FRAMES * FRAME_SIZE] = { 0 }; // 有PLC
int16_t without_plc_pcm[TOTAL_FRAMES * FRAME_SIZE] = { 0 }; // 无PLC
int16_t with_plc_pcm_g711[TOTAL_FRAMES * FRAME_SIZE] = { 0 }; // 有PLC


int main(int argc, char * argv[]) 
{
    int rate=30;
    int loss_count = 0;
    int total_samples = 0;
    AudioFrame history = { 0 };
    AudioFrame output;
    LowcFE_c g711_lpc = { 0 };

    const int MAX_RAW_SAMPLES = 4096;  // 根据需要调整大小
    int16_t* raw_pcm_buf = malloc(MAX_RAW_SAMPLES * sizeof(int16_t));
    int16_t* mono_pcm_buf = malloc(MAX_RAW_SAMPLES * sizeof(int16_t));

    cvsd_g711plc_construct(&g711_lpc);

    if (argc > 1) {
        rate = atoi(argv[1]);
        if (rate>100 || rate<0)
            printf("Lost rate must between 0-100\n");
        else
            printf("Lost rate %d%%\n", rate);
    }
    else {
        printf("Usage: plc <lost rate in percentage>\n");
        exit(-1);
    }

    if (!raw_pcm_buf || !mono_pcm_buf) {
        printf("Memory allocation failed\n");
        return 1;
    }

    srand((unsigned int)time(NULL));

    if (!init_wav_input("input.wav")) {
        free(raw_pcm_buf);
        free(mono_pcm_buf);
        return 1;
    }

    // 在main函数中添加缓冲区，保存所有输出帧
    int frame_idx = 0;

    g_playback_active = true;
    // 循环中填充缓冲区（替代原queue_buffer逻辑，或并行执行）
    while (g_playback_active) {
        int samples_read = read_wav_8khz(history.pcm, FRAME_SIZE, raw_pcm_buf, mono_pcm_buf, MAX_RAW_SAMPLES);
        if (samples_read < FRAME_SIZE) break;

        bool is_lost = (rand()%100)<rate;
        loss_count = is_lost ? loss_count + 1 : 0;

        // 1. 参考音频（无丢包）
        memcpy(&ref_pcm[frame_idx * FRAME_SIZE], history.pcm, FRAME_SIZE * sizeof(int16_t));

        // 2. 有PLC的输出
        plc_process(&output, &history, is_lost, loss_count);
        memcpy(&with_plc_pcm[frame_idx * FRAME_SIZE], output.pcm, FRAME_SIZE * sizeof(int16_t));

        // 3. 无PLC的输出（丢包时用静音替代）
        int16_t no_plc_output[FRAME_SIZE];
        int16_t g711_pcm[FRAME_SIZE];
        if (is_lost) {
            printf(":Droped\n");
            memset(no_plc_output, 0, FRAME_SIZE * sizeof(int16_t)); // 静音
            memset(g711_pcm, 0, FRAME_SIZE * sizeof(int16_t)); // 静音
        }
        else {
            printf("\n");
            memcpy(no_plc_output, history.pcm, FRAME_SIZE * sizeof(int16_t));
            memcpy(g711_pcm, history.pcm, FRAME_SIZE * sizeof(int16_t));
        }
        memcpy(&without_plc_pcm[frame_idx * FRAME_SIZE], no_plc_output, FRAME_SIZE * sizeof(int16_t));
        //4. g711 plc
        if (is_lost) {

            g711plc_dofe(&g711_lpc, (uint8_t*)g711_pcm);
        }
        else {
            g711plc_addtohistory(&g711_lpc, (uint8_t*)g711_pcm);
        }
        memcpy(&with_plc_pcm_g711[frame_idx * FRAME_SIZE], g711_pcm, FRAME_SIZE * sizeof(int16_t));

        frame_idx++;
        total_samples += FRAME_SIZE;
    }

    // 循环结束后写入文件
    char fn_plc[80];
    char fn_plc_g711[80];
    char fn_no_plc[80];

    strcpy(fn_plc, "log\\");
    strcat(fn_plc, argv[1]);
    strcat(fn_plc, "_with_plc.wav");

    strcpy(fn_plc_g711, "log\\");
    strcat(fn_plc_g711, argv[1]);
    strcat(fn_plc_g711, "_with_plc_g711.wav");

    strcpy(fn_no_plc, "log\\");
    strcat(fn_no_plc, argv[1]);
    strcat(fn_no_plc, "_without_plc.wav");

    write_wav("reference.wav", ref_pcm, total_samples, SAMPLE_RATE);
    write_wav(fn_plc, with_plc_pcm, total_samples, SAMPLE_RATE);
    write_wav(fn_no_plc, without_plc_pcm, total_samples, SAMPLE_RATE);
    write_wav(fn_plc_g711, with_plc_pcm_g711, total_samples, SAMPLE_RATE);
}
#endif