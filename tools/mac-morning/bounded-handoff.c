#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

extern char **environ;
static double monotonic(void) {
 struct timespec t;
 clock_gettime(CLOCK_MONOTONIC,&t);
 return t.tv_sec+t.tv_nsec/1000000000.0;
}
static void pause_poll(void) {
 struct timespec t={0,50000000};
 nanosleep(&t,0);
}
int main(int argc,char **argv) {
 if(argc<2 || argv[1][0]!='/')return 64;
 posix_spawn_file_actions_t actions;
 posix_spawn_file_actions_init(&actions);
 posix_spawn_file_actions_addopen(&actions,STDOUT_FILENO,"/dev/null",O_WRONLY,0);
 posix_spawn_file_actions_addopen(&actions,STDERR_FILENO,"/dev/null",O_WRONLY,0);
 pid_t child;
 int started=posix_spawn(&child,argv[1],&actions,0,&argv[1],environ);
 posix_spawn_file_actions_destroy(&actions);
 if(started)return 65;
 int status=0;
 double deadline=monotonic()+5.0;
 while(monotonic()<deadline){
  pid_t result=waitpid(child,&status,WNOHANG);
  if(result==child)return WIFEXITED(status)?WEXITSTATUS(status):66;
  if(result<0 && errno!=EINTR)return 66;
  pause_poll();
 }
 // Only our own still-unreaped child can be signalled; never the existing
 // browser, another process, a process name or a process group.
 pid_t result=waitpid(child,&status,WNOHANG);
 if(result==child)return WIFEXITED(status)?WEXITSTATUS(status):66;
 if(result<0)return 66;
 kill(child,SIGTERM);
 deadline=monotonic()+0.5;
 while(monotonic()<deadline){
  result=waitpid(child,&status,WNOHANG);
  if(result==child)return 66;
  if(result<0 && errno!=EINTR)return 66;
  pause_poll();
 }
 result=waitpid(child,&status,WNOHANG);
 if(result==0){kill(child,SIGKILL);while(waitpid(child,&status,0)<0 && errno==EINTR){}}
 return 66;
}
