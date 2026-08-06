/* read-only EtherCAT slave enumerator — finds RH56F1 without moving it */
#include "soem/soem.h"
#include <stdio.h>
#include <string.h>
static ecx_contextt ctx;
int main(int argc, char **argv){
    if(argc<2){fprintf(stderr,"usage: %s <iface>\n",argv[0]);return 2;}
    if(!ecx_init(&ctx, argv[1])){
        fprintf(stderr,"ecx_init failed on %s (need CAP_NET_RAW or root)\n",argv[1]);return 1;}
    int n = ecx_config_init(&ctx);
    if(n<=0){
        printf("NO EtherCAT slave on %s (config_init=%d) — check cable/power on that link\n",argv[1],n);
        ecx_close(&ctx);return 3;}
    printf("FOUND %d EtherCAT slave(s) on %s:\n", n, argv[1]);
    for(int i=1;i<=n;i++){
        printf("  slave %d  name=\"%s\"  vendor=0x%08X product=0x%08X rev=0x%08X  state=0x%02X  Obytes=%d Ibytes=%d\n",
            i, ctx.slavelist[i].name,
            (unsigned)ctx.slavelist[i].eep_man,
            (unsigned)ctx.slavelist[i].eep_id,
            (unsigned)ctx.slavelist[i].eep_rev,
            ctx.slavelist[i].state,
            ctx.slavelist[i].Obytes, ctx.slavelist[i].Ibytes);
    }
    ecx_close(&ctx);
    return 0;
}
