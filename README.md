# Patronus
Patronus was designed with penetration testers in mind! This dynamic tool captures command line inputs during security assessments, meticulously redacts any sensitive information, and organizes the data by command type. All the organized commands are then displayed through a user-friendly, interactive web interface, making it easy to review and manage. With Patronus, penetration testers can cast away the shadows of data mishandling and ensure a secure and streamlined workflow.

This tool is still a work in progress, any feedback is welcomed

## Usage

Install with pipx
```
pipx install git+https://github.com/Michaeladsl/Patronusx
```


When performing the on command for the first time, if Asciinema is not installed it will download it for you.

Configure your zsh environment for automatic recordings.
```
patronus on
```
```
patronus off
```
![pull_on](https://github.com/Michaeladsl/Patronus/assets/89179287/7a2ff40d-4058-4e1b-9bbc-fc63805731e7)





By default, patronus will run the redact, split, and server scripts. This redacts any recordings in the full directory, splits the newly redacted files into individual command recordings, and launches the flask server.
```
patronus
```
![demo1](https://github.com/Michaeladsl/Patronus/assets/89179287/096e32e1-47cf-429b-ab12-b3c5e7e1a8db)


![directories](https://github.com/Michaeladsl/Patronus/assets/89179287/2a33982d-032d-4a0c-82c4-508db7bddb25)


The web browser easily sorts by command and allows the user to delete the recording or copy any text out of the displayed recording and redact that information.

![serverexample](https://github.com/Michaeladsl/Patronus/assets/89179287/11bbaab2-473e-4315-befa-18f302dd1515)





Patronus allows for individual running of tools
```
patronus --run redact,split,server,config
```


## Recordings
The scripts look for the necessary files in the static directory when running. If configuring outside of using the 'on' option, make sure the full scritps are stored in static/full and create the static/redacted_full and the static/splits directories.
