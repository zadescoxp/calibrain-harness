#ifndef CALIBRAIN_LOGGER_MQH
#define CALIBRAIN_LOGGER_MQH

class CCalibrainLogger
  {
private:
   int               m_file;
   bool              m_file_enabled;

   string Clean(const string value)
     {
      string out=value;
      StringReplace(out,"\r"," ");
      StringReplace(out,"\n"," ");
      StringReplace(out,";",",");
      return out;
     }

public:
                     CCalibrainLogger(void)
     {
      m_file=INVALID_HANDLE;
      m_file_enabled=false;
     }

   bool Init(void)
     {
      FolderCreate("CalibrainAgents");
      m_file=FileOpen("CalibrainAgents\\calibrain.csv",
                      FILE_READ|FILE_WRITE|FILE_CSV|FILE_SHARE_READ|FILE_SHARE_WRITE,
                      ';',CP_UTF8);
      if(m_file==INVALID_HANDLE)
        {
         PrintFormat("Calibrain logger: CSV unavailable (error %d); journal logging remains active",
                     GetLastError());
         return false;
        }
      m_file_enabled=true;
      if(FileSize(m_file)==0)
         FileWrite(m_file,"time","level","symbol","component","message");
      FileSeek(m_file,0,SEEK_END);
      return true;
     }

   void Write(const string level,const string symbol,const string component,const string message)
     {
      string stamp=TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS);
      string safe_message=Clean(message);
      PrintFormat("[%s] %s %s %s: %s",stamp,level,symbol,component,safe_message);
      if(m_file_enabled && m_file!=INVALID_HANDLE)
        {
         FileWrite(m_file,stamp,level,symbol,component,safe_message);
         FileFlush(m_file);
        }
     }

   void Deinit(void)
     {
      if(m_file!=INVALID_HANDLE)
        {
         FileFlush(m_file);
         FileClose(m_file);
         m_file=INVALID_HANDLE;
        }
      m_file_enabled=false;
     }
  };

#endif
