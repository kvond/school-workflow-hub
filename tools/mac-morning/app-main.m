#import <Cocoa/Cocoa.h>

// A local app, not AppleScript: no Automation grant or Terminal window.
int main(void) {
 @autoreleasepool {
  [NSApplication sharedApplication];
  NSString *launcher=[[NSBundle mainBundle] pathForResource:@"launch" ofType:@"sh"];
  NSTask *task=[NSTask new];
  task.executableURL=[NSURL fileURLWithPath:@"/bin/sh"];
  task.arguments=@[launcher ?: @""];
  task.standardOutput=[NSFileHandle fileHandleWithNullDevice];
  task.standardError=[NSFileHandle fileHandleWithNullDevice];
  NSError *error=nil;
  BOOL started=launcher && [task launchAndReturnError:&error];
  if(started)[task waitUntilExit];
  if(!started || task.terminationStatus!=0){
   NSAlert *alert=[NSAlert new];
   alert.messageText=@"School morning needs attention";
   alert.informativeText=@"The school browser could not be opened safely. Open Chrome’s existing School profile and the Hub helper. Existing tabs were preserved.";
   [alert addButtonWithTitle:@"OK"];
   [NSApp activateIgnoringOtherApps:YES];
   [alert runModal];
   return 1;
  }
 }
 return 0;
}
